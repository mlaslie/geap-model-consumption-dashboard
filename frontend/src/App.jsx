import React, { useState, useEffect, useRef } from 'react';
import SummaryCards from './components/SummaryCards';
import UsageCharts from './components/UsageCharts';
import UserTable from './components/UserTable';
import BudgetManager from './components/BudgetManager';
import BudgetConsumption from './components/BudgetConsumption';
import MonthlyStatement from './components/MonthlyStatement';
import AlertCenter from './components/AlertCenter';
import FinOpsAssistant from './components/FinOpsAssistant';
import ModelLogging from './components/ModelLogging';
import PricingPlanner from './components/PricingPlanner';
import CostAnomalyBanner from './components/CostAnomalyBanner';
import ConfigHealth from './components/ConfigHealth';
import { apiFetch, setApiToken } from './utils/api';
import { formatDateTime } from './utils/formatters';


const VALID_TABS = [
  'dashboard', 'budgets', 'budget-consumption', 'statements', 'planner', 'alerts', 'logging', 'assistant',
];

// Dashboard time ranges → trailing-day window sent to /api/usage.
const RANGE_TO_DAYS = { today: 1, week: 7, month: 30, '6months': 183, year: 366 };
const RANGE_LABELS = {
  today: 'Today', week: 'Last 7 days', month: 'Last 30 days',
  '6months': 'Last 6 months', year: 'Last 12 months',
};

export default function App() {
  // Tabs are deep-linkable: /#planner opens the Pricing & Planner tab directly.
  const [activeTab, setActiveTabState] = useState(() => {
    const hash = window.location.hash.replace('#', '');
    return VALID_TABS.includes(hash) ? hash : 'dashboard';
  });
  const setActiveTab = (tab) => {
    setActiveTabState(tab);
    window.history.replaceState(null, '', `#${tab}`);
  };
  const [logs, setLogs] = useState([]);
  const [budgets, setBudgets] = useState({});
  const [alerts, setAlerts] = useState([]);
  const [budgetStatus, setBudgetStatus] = useState({});
  const [projectId, setProjectId] = useState(null);
  const [loggingConfig, setLoggingConfig] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [apiError, setApiError] = useState(null);
  const [truncated, setTruncated] = useState(false);
  // Stores the model list the user dismissed; if a DIFFERENT unpriced model
  // appears later in the session, the notice returns (a plain boolean would
  // hide genuinely new models behind an old dismissal).
  const [dismissedUnpricedSig, setDismissedUnpricedSig] = useState(null);
  // Cost-anomaly and config-health data from the two new backend detectors.
  const [costAnomaly, setCostAnomaly] = useState(null);
  const [configHealth, setConfigHealth] = useState(null);
  // Dismissal key for the anomaly banner: generated_at_utc + '|' + ratio.
  // A new spike with a different ratio or generation timestamp re-surfaces the banner.
  const [dismissedAnomalyKey, setDismissedAnomalyKey] = useState(null);
  // Config-health findings are long-lived (unlike a cost spike), so the
  // dismissal persists across reloads — keyed by the issue SET so genuinely
  // new findings re-surface rather than hiding behind an old dismissal.
  const [dismissedConfigSig, setDismissedConfigSig] = useState(
    () => localStorage.getItem('portal_dismissed_config_sig') || null
  );
  const [showAuthModal, setShowAuthModal] = useState(false);
  const [authTokenInput, setAuthTokenInput] = useState('');

  // Theme: 'light' | 'dark' | 'system' — persisted in localStorage
  const [theme, setTheme] = useState(() => localStorage.getItem('portal_theme') || 'system');

  // Filter States
  const [selectedPrincipal, setSelectedPrincipal] = useState('all');
  const [selectedModel, setSelectedModel] = useState('all');

  // Chart range state — 'today'|'week'|'month'|'6months'|'year'
  const [chartRange, setChartRange] = useState('month');
  // chartLogs: extended-range rows fetched separately (null = reuse filtered `logs`)
  const [chartLogs, setChartLogs] = useState(null);
  const [chartLoading, setChartLoading] = useState(false);
  // True when an extended-range chart fetch hit the backend row cap
  const [chartTruncated, setChartTruncated] = useState(false);

  // Sync-status state
  // syncState: 'idle' | 'running' | 'success' | 'error'
  const [syncState, setSyncState] = useState('idle');
  const [lastSyncAt, setLastSyncAt] = useState(null);
  // syncLog entries: { time: ISO string, ok: bool, summary: string, detail: string|null, source: 'initial'|'manual'|'auto' }
  const [syncLog, setSyncLog] = useState([]);
  const [showSyncLog, setShowSyncLog] = useState(false);

  const syncTimerRef = useRef(null);
  const syncLogRef = useRef(null);

  // Apply theme to document root whenever it changes
  useEffect(() => {
    localStorage.setItem('portal_theme', theme);
    if (theme === 'dark') {
      document.documentElement.setAttribute('data-theme', 'dark');
    } else if (theme === 'light') {
      document.documentElement.setAttribute('data-theme', 'light');
    } else {
      document.documentElement.removeAttribute('data-theme');
    }
  }, [theme]);

  // Fetch initial telemetry and config on mount
  useEffect(() => {
    fetchData();

    // Auto-refresh logs and alerts every 30 seconds to capture streaming data!
    const interval = setInterval(() => {
      refreshData('auto');
    }, 30000);

    return () => clearInterval(interval);
  }, []);

  // Listen for auth-required events dispatched by apiFetch on 401
  useEffect(() => {
    const handleAuthRequired = () => setShowAuthModal(true);
    window.addEventListener('portal-auth-required', handleAuthRequired);
    return () => window.removeEventListener('portal-auth-required', handleAuthRequired);
  }, []);

  // Follow manual URL-hash edits (e.g. pasting /#alerts into the address bar)
  useEffect(() => {
    const handleHashChange = () => {
      const hash = window.location.hash.replace('#', '');
      if (VALID_TABS.includes(hash)) setActiveTabState(hash);
    };
    window.addEventListener('hashchange', handleHashChange);
    return () => window.removeEventListener('hashchange', handleHashChange);
  }, []);

  // Fetch usage for the selected range whenever it differs from the default
  // 30-day feed. Every range except "month" is fetched server-side so the
  // range scopes the WHOLE dashboard (summary cards and the per-principal
  // table included), not just the chart's client-side bucketing — a table
  // cannot bucket away out-of-range rows the way the chart can.
  useEffect(() => {
    const days = RANGE_TO_DAYS[chartRange] || 30;
    if (days === 30) {
      // "month" is exactly the default feed already in `logs`.
      setChartLogs(null);
      setChartLoading(false);
      setChartTruncated(false);
      return;
    }
    let cancelled = false;
    setChartLoading(true);
    apiFetch(`/api/usage?days=${days}`)
      .then(res => res.json())
      .then(data => {
        if (cancelled) return;
        if (data.status === 'success') {
          setChartLogs(data.data);
          setChartTruncated(data.truncated === true);
        }
      })
      .catch(e => {
        if (!cancelled && e.message !== 'Unauthorized') {
          console.error('Chart range fetch failed:', e);
        }
      })
      .finally(() => {
        if (!cancelled) setChartLoading(false);
      });
    return () => { cancelled = true; };
  }, [chartRange]);

  // Close sync-log dropdown on outside click or Escape
  useEffect(() => {
    if (!showSyncLog) return;
    const onMouseDown = (e) => {
      if (syncLogRef.current && !syncLogRef.current.contains(e.target)) {
        setShowSyncLog(false);
      }
    };
    const onKeyDown = (e) => {
      if (e.key === 'Escape') setShowSyncLog(false);
    };
    document.addEventListener('mousedown', onMouseDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onMouseDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [showSyncLog]);

  // Single-flight guard: only one sync (initial/manual/auto) may run at a
  // time. Without this, a slow sync overlaps the next auto tick and the OLDER
  // completion clobbers the newer run's data and status (and its 4s revert
  // timer can flip the UI to idle mid-sync).
  const syncInFlightRef = useRef(false);
  const mountedRef = useRef(true);

  // Cleanup sync revert timer on unmount; mark unmounted so late async
  // completions don't setState or install new timers on a dead tree.
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (syncTimerRef.current) clearTimeout(syncTimerRef.current);
    };
  }, []);

  // Schedule revert of syncState to 'idle' after 4 s; clears any pending revert first.
  const scheduleSyncRevert = () => {
    if (!mountedRef.current) return;
    if (syncTimerRef.current) clearTimeout(syncTimerRef.current);
    syncTimerRef.current = setTimeout(() => {
      if (mountedRef.current) setSyncState('idle');
    }, 4000);
  };

  // Append a log entry (newest-first, capped at 50).
  const pushSyncEntry = (entry) => {
    setSyncLog((prev) => [entry, ...prev].slice(0, 50));
  };

  const fetchData = async () => {
    if (syncInFlightRef.current) return;
    syncInFlightRef.current = true;
    setIsLoading(true);
    setApiError(null);
    if (syncTimerRef.current) clearTimeout(syncTimerRef.current);
    setSyncState('running');
    const now = new Date().toISOString();
    try {
      const [usageRes, budgetsRes, alertsRes, loggingRes, budgetStatusRes, anomalyRes, configHealthRes] = await Promise.all([
        apiFetch('/api/usage'),
        apiFetch('/api/budgets'),
        apiFetch('/api/alerts'),
        apiFetch('/api/logging-config'),
        apiFetch('/api/budget-status'),
        // .catch(null): a detector outage must never reject the batch and
        // take the primary dashboard data down with it.
        apiFetch('/api/cost-anomalies?baseline_days=7').catch(() => null),
        apiFetch('/api/config-health').catch(() => null),
      ]);

      const errors = [];
      const summaryParts = [];

      const usageData = await usageRes.json();
      if (!usageRes.ok) {
        errors.push(`usage: ${usageData.detail || usageRes.statusText}`);
      } else if (usageData.status === 'success') {
        setLogs(usageData.data);
        setTruncated(usageData.truncated === true);
        if (usageData.project_id) setProjectId(usageData.project_id);
        summaryParts.push(`usage (${usageData.data.length} rows)`);
      }

      const budgetsData = await budgetsRes.json();
      if (!budgetsRes.ok) {
        errors.push(`budgets: ${budgetsData.detail || budgetsRes.statusText}`);
      } else if (budgetsData.status === 'success') {
        setBudgets(budgetsData.data);
        summaryParts.push(`budgets (${Object.keys(budgetsData.data).length})`);
      }

      const alertsData = await alertsRes.json();
      if (!alertsRes.ok) {
        errors.push(`alerts: ${alertsData.detail || alertsRes.statusText}`);
      } else if (alertsData.status === 'success') {
        setAlerts(alertsData.data);
        summaryParts.push(`alerts (${alertsData.data.length})`);
      }

      const loggingData = await loggingRes.json();
      if (!loggingRes.ok) {
        errors.push(`logging-config: ${loggingData.detail || loggingRes.statusText}`);
      } else if (loggingData.status === 'success') {
        setLoggingConfig(loggingData.data);
        summaryParts.push('logging config');
      }

      const budgetStatusData = await budgetStatusRes.json();
      if (!budgetStatusRes.ok) {
        errors.push(`budget-status: ${budgetStatusData.detail || budgetStatusRes.statusText}`);
      } else if (budgetStatusData.status === 'success') {
        setBudgetStatus(budgetStatusData.data);
        summaryParts.push(`budget-status (${Object.keys(budgetStatusData.data).length} principals)`);
      }

      // Cost-anomaly and config-health: failures are silent (no error banner, no errors[]).
      try {
        const anomalyData = await anomalyRes.json();
        if (anomalyRes.ok && anomalyData.status === 'success') {
          setCostAnomaly(anomalyData.data);
          summaryParts.push('cost-anomalies');
        } else {
          setCostAnomaly(null);
        }
      } catch {
        setCostAnomaly(null);
      }

      try {
        const configHealthData = await configHealthRes.json();
        if (configHealthRes.ok && configHealthData.status === 'success') {
          setConfigHealth(configHealthData.data);
          summaryParts.push('config-health');
        } else {
          setConfigHealth(null);
        }
      } catch {
        setConfigHealth(null);
      }

      if (errors.length > 0) {
        setApiError(`Failed to load usage data: ${errors.join('; ')}`);
        setSyncState('error');
        pushSyncEntry({ time: now, ok: false, summary: `Failed: ${errors.join('; ')}`, detail: errors.join('\n'), source: 'initial' });
      } else {
        setLastSyncAt(now);
        setSyncState('success');
        pushSyncEntry({ time: now, ok: true, summary: `Synced ${summaryParts.join(', ')}`, detail: null, source: 'initial' });
      }
      scheduleSyncRevert();
    } catch (e) {
      if (e.message !== 'Unauthorized') {
        console.error("Error loading API telemetry:", e);
        setApiError(`Failed to load usage data: ${e.message}`);
        setSyncState('error');
        pushSyncEntry({ time: now, ok: false, summary: `Error: ${e.message}`, detail: e.message, source: 'initial' });
        scheduleSyncRevert();
      } else {
        setSyncState('idle');
      }
    } finally {
      syncInFlightRef.current = false;
      if (mountedRef.current) setIsLoading(false);
    }
  };

  const refreshData = async (source = 'manual') => {
    if (syncInFlightRef.current) return;
    syncInFlightRef.current = true;
    if (syncTimerRef.current) clearTimeout(syncTimerRef.current);
    setSyncState('running');
    const now = new Date().toISOString();
    try {
      const [usageRes, alertsRes, loggingRes, budgetStatusRes, anomalyRes, configHealthRes] = await Promise.all([
        apiFetch('/api/usage'),
        apiFetch('/api/alerts'),
        apiFetch('/api/logging-config'),
        apiFetch('/api/budget-status'),
        // .catch(null): a detector outage must never reject the batch and
        // take the primary dashboard data down with it.
        apiFetch('/api/cost-anomalies?baseline_days=7').catch(() => null),
        apiFetch('/api/config-health').catch(() => null),
      ]);

      const errors = [];
      const summaryParts = [];

      const usageData = await usageRes.json();
      if (!usageRes.ok) {
        errors.push(`usage: ${usageData.detail || usageRes.statusText}`);
      } else if (usageData.status === 'success') {
        setLogs(usageData.data);
        setTruncated(usageData.truncated === true);
        if (usageData.project_id) setProjectId(usageData.project_id);
        summaryParts.push(`usage (${usageData.data.length} rows)`);
      }

      const alertsData = await alertsRes.json();
      if (!alertsRes.ok) {
        errors.push(`alerts: ${alertsData.detail || alertsRes.statusText}`);
      } else if (alertsData.status === 'success') {
        setAlerts(alertsData.data);
        summaryParts.push(`alerts (${alertsData.data.length})`);
      }

      const loggingData = await loggingRes.json();
      if (!loggingRes.ok) {
        errors.push(`logging-config: ${loggingData.detail || loggingRes.statusText}`);
      } else if (loggingData.status === 'success') {
        setLoggingConfig(loggingData.data);
        summaryParts.push('logging config');
      }

      const budgetStatusData = await budgetStatusRes.json();
      if (!budgetStatusRes.ok) {
        errors.push(`budget-status: ${budgetStatusData.detail || budgetStatusRes.statusText}`);
      } else if (budgetStatusData.status === 'success') {
        setBudgetStatus(budgetStatusData.data);
        summaryParts.push(`budget-status (${Object.keys(budgetStatusData.data).length} principals)`);
      }

      // Cost-anomaly and config-health: failures are silent (no error banner, no errors[]).
      try {
        const anomalyData = await anomalyRes.json();
        if (anomalyRes.ok && anomalyData.status === 'success') {
          setCostAnomaly(anomalyData.data);
          summaryParts.push('cost-anomalies');
        } else {
          setCostAnomaly(null);
        }
      } catch {
        setCostAnomaly(null);
      }

      try {
        const configHealthData = await configHealthRes.json();
        if (configHealthRes.ok && configHealthData.status === 'success') {
          setConfigHealth(configHealthData.data);
          summaryParts.push('config-health');
        } else {
          setConfigHealth(null);
        }
      } catch {
        setConfigHealth(null);
      }

      if (errors.length > 0) {
        setApiError(`Failed to load usage data: ${errors.join('; ')}`);
        setSyncState('error');
        pushSyncEntry({ time: now, ok: false, summary: `Failed: ${errors.join('; ')}`, detail: errors.join('\n'), source });
      } else {
        setApiError(null);
        setLastSyncAt(now);
        setSyncState('success');
        pushSyncEntry({ time: now, ok: true, summary: `Synced ${summaryParts.join(', ')}`, detail: null, source });
      }
      scheduleSyncRevert();
    } catch (e) {
      if (e.message !== 'Unauthorized') {
        console.error("Error refreshing API telemetry:", e);
        setApiError(`Failed to load usage data: ${e.message}`);
        setSyncState('error');
        pushSyncEntry({ time: now, ok: false, summary: `Error: ${e.message}`, detail: e.message, source });
        scheduleSyncRevert();
      } else {
        setSyncState('idle');
      }
    } finally {
      syncInFlightRef.current = false;
    }
  };

  const handleSaveBudgets = async (updatedBudgets) => {
    try {
      const response = await apiFetch('/api/budgets', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updatedBudgets)
      });
      const data = await response.json();
      if (!response.ok) {
        setApiError(`Failed to save budgets: ${data.detail || response.statusText}`);
        return false;
      }
      if (data.status === 'success') {
        setBudgets(updatedBudgets);
        // Refresh alert validations and budget-status immediately
        const [alertsRes, budgetStatusRes] = await Promise.all([
          apiFetch('/api/alerts'),
          apiFetch('/api/budget-status'),
        ]);
        const alertsData = await alertsRes.json();
        if (!alertsRes.ok) {
          setApiError(`Failed to load usage data: alerts: ${alertsData.detail || alertsRes.statusText}`);
        } else if (alertsData.status === 'success') {
          setAlerts(alertsData.data);
        }
        const budgetStatusData = await budgetStatusRes.json();
        if (!budgetStatusRes.ok) {
          setApiError(`Failed to load usage data: budget-status: ${budgetStatusData.detail || budgetStatusRes.statusText}`);
        } else if (budgetStatusData.status === 'success') {
          setBudgetStatus(budgetStatusData.data);
        }
        return true;
      }
      return false;
    } catch (e) {
      if (e.message !== 'Unauthorized') {
        console.error("Error saving budgets:", e);
        setApiError(`Failed to save budgets: ${e.message}`);
      }
      return false;
    }
  };

  const handleAuthSave = () => {
    const trimmed = authTokenInput.trim();
    if (!trimmed) return;
    setApiToken(trimmed);
    setAuthTokenInput('');
    setShowAuthModal(false);
    fetchData();
  };

  // Derive unique models with no pricing (pricing_match === 'default') across all logs
  const unpricedModels = Array.from(new Set(
    logs.filter(log => log.pricing_match === 'default').map(log => log.model_name).filter(Boolean)
  )).sort();

  // Derive unique principals and models for dropdown filters
  const uniquePrincipals = Array.from(new Set(logs.map(log => log.user_email).filter(Boolean))).sort();
  const uniqueModels = Array.from(new Set(logs.map(log => log.model_name).filter(Boolean))).sort();

  // Range-scoped rows drive the ENTIRE dashboard (summary cards, chart, and
  // the per-principal table) so every figure on the page covers the same
  // window. chartLogs holds the selected range; null means "month", which is
  // exactly the default `logs` feed.
  const rangeRows = chartLogs ?? logs;
  const dashboardRows = rangeRows.filter(log => {
    const matchPrincipal = selectedPrincipal === 'all' || log.user_email === selectedPrincipal;
    const matchModel = selectedModel === 'all' || log.model_name === selectedModel;
    return matchPrincipal && matchModel;
  });
  const rangeLabel = RANGE_LABELS[chartRange] || 'Last 30 days';

  // Anomaly banner visibility. The key must be STABLE across refreshes:
  // generated_at_utc changes on every request and the dashboard polls every
  // 30s, so keying on it made a dismissal last only until the next poll.
  // Key instead on the UTC DAY plus the set of spiking principals — the same
  // spike stays dismissed, while a new day or a newly-spiking principal
  // re-surfaces the banner.
  const anomalyKey = costAnomaly && costAnomaly.is_anomaly
    ? `${(costAnomaly.generated_at_utc || '').slice(0, 10)}|${
        (costAnomaly.per_user || [])
          .filter(u => u.is_anomaly)
          .map(u => u.user_email)
          .sort()
          .join(',') || 'fleet'
      }`
    : null;
  const showAnomalyBanner = anomalyKey !== null && dismissedAnomalyKey !== anomalyKey;

  // Signature over every finding: any added/removed item re-expands the panel.
  const configHealthSig = configHealth
    ? [
        ...(configHealth.unlogged_used_models || []),
        ...(configHealth.used_unpriced_models || []),
        ...(configHealth.unused_budgets || []).map(b => b.identity),
        ...(configHealth.logged_unused_models || []),
      ].sort().join(',')
    : null;
  const configHealthDismissed =
    configHealthSig !== null && dismissedConfigSig === configHealthSig;
  const handleDismissConfigHealth = () => {
    setDismissedConfigSig(configHealthSig);
    try { localStorage.setItem('portal_dismissed_config_sig', configHealthSig); } catch { /* storage unavailable */ }
  };
  const handleRestoreConfigHealth = () => {
    setDismissedConfigSig(null);
    try { localStorage.removeItem('portal_dismissed_config_sig'); } catch { /* storage unavailable */ }
  };

  const themeOptions = [
    { value: 'light',  label: '☀ Light' },
    { value: 'system', label: '⊙ Auto'  },
    { value: 'dark',   label: '◑ Dark'  },
  ];

  // Force Refresh button content based on syncState
  let refreshButtonContent;
  if (syncState === 'running') {
    refreshButtonContent = (
      <>
        <span className="spin-motion" aria-hidden="true">⟳</span>
        <span className="spin-reduced" aria-hidden="true">…</span>
        <span>Syncing</span>
      </>
    );
  } else if (syncState === 'success') {
    refreshButtonContent = <span style={{ color: 'var(--status-good)' }}>✓ Synced</span>;
  } else if (syncState === 'error') {
    refreshButtonContent = <span style={{ color: 'var(--status-crit)' }}>✗ Failed</span>;
  } else {
    refreshButtonContent = '🔄 Force Refresh';
  }

  return (
    <div className="app-container">
      {/* Auth Token Modal */}
      {showAuthModal && (
        <div style={{
          position: 'fixed',
          inset: 0,
          backgroundColor: 'rgba(0, 0, 0, 0.6)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          zIndex: 1000
        }}>
          <div style={{
            background: 'var(--surface)',
            border: '1px solid var(--line)',
            borderRadius: 'var(--radius)',
            padding: '32px',
            width: '100%',
            maxWidth: '420px',
            display: 'flex',
            flexDirection: 'column',
            gap: '16px',
            boxShadow: 'var(--shadow-lg)'
          }}>
            <h2 style={{ margin: 0, fontSize: '18px', fontWeight: '600', color: 'var(--ink)' }}>
              API token required
            </h2>
            <p style={{ margin: 0, fontSize: '13px', color: 'var(--ink-2)' }}>
              This portal requires an authorization token. Enter it below to continue.
            </p>
            <input
              type="password"
              placeholder="Bearer token..."
              className="form-control"
              value={authTokenInput}
              onChange={(e) => setAuthTokenInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleAuthSave()}
              autoFocus
            />
            <button
              className="btn-primary"
              onClick={handleAuthSave}
              disabled={!authTokenInput.trim()}
            >
              Save
            </button>
          </div>
        </div>
      )}

      {/* Sidebar Navigation */}
      <div className="sidebar">
        <div className="app-logo">
          <div className="logo-icon">✨</div>
          <div className="logo-text">CONFIGURATION</div>
        </div>

        <nav className="nav-menu">
          <button
            className={`nav-item ${activeTab === 'dashboard' ? 'active' : ''}`}
            onClick={() => setActiveTab('dashboard')}
          >
            <span className="nav-item-icon">📈</span> Dashboard Overview
          </button>
          <button
            className={`nav-item ${activeTab === 'budgets' ? 'active' : ''}`}
            onClick={() => setActiveTab('budgets')}
          >
            <span className="nav-item-icon">💼</span> Budget Constraints
          </button>
          <button
            className={`nav-item ${activeTab === 'budget-consumption' ? 'active' : ''}`}
            onClick={() => setActiveTab('budget-consumption')}
          >
            <span className="nav-item-icon">📊</span> Budget Consumption
          </button>
          <button
            className={`nav-item ${activeTab === 'statements' ? 'active' : ''}`}
            onClick={() => setActiveTab('statements')}
          >
            <span className="nav-item-icon">🧾</span> Monthly Statement
          </button>
          <button
            className={`nav-item ${activeTab === 'planner' ? 'active' : ''}`}
            onClick={() => setActiveTab('planner')}
          >
            <span className="nav-item-icon">💰</span> Pricing &amp; Planner
          </button>
          <button
            className={`nav-item ${activeTab === 'alerts' ? 'active' : ''}`}
            onClick={() => setActiveTab('alerts')}
          >
            <span className="nav-item-icon">🔔</span> Alerts Center
          </button>
          <button
            className={`nav-item ${activeTab === 'logging' ? 'active' : ''}`}
            onClick={() => setActiveTab('logging')}
          >
            <span className="nav-item-icon">📡</span> Model Logging
          </button>
          <button
            className={`nav-item ${activeTab === 'assistant' ? 'active' : ''}`}
            onClick={() => setActiveTab('assistant')}
          >
            <span className="nav-item-icon">✨</span> Gemini FinOps AI
          </button>
        </nav>

        <div className="sidebar-footer">
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <span style={{ fontSize: '9px', color: 'var(--status-good)' }}>●</span>
            <span style={{ color: 'var(--ink-2)', fontSize: '11.5px' }}>Active GCP Project</span>
          </div>
          <div className="project-id-badge">{projectId ?? '—'}</div>

          {/* Theme toggle — Light / Auto / Dark */}
          <div className="theme-toggle" role="group" aria-label="Color theme">
            {themeOptions.map(({ value, label }) => (
              <button
                key={value}
                className={`theme-toggle-btn${theme === value ? ' active' : ''}`}
                onClick={() => setTheme(value)}
                aria-pressed={theme === value}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Main Content Pane */}
      <div className="main-content">
        <div className="main-header-bar" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div>
            <h1 className="main-title">
              {activeTab === 'dashboard' && "Agent Platform Model Telemetry"}
              {activeTab === 'budgets' && "Corporate Budget Manager"}
              {activeTab === 'budget-consumption' && "Budget Consumption"}
              {activeTab === 'statements' && "Monthly Statement"}
              {activeTab === 'alerts' && "Proactive Alerts Center"}
              {activeTab === 'logging' && "Vertex AI Model Logging"}
              {activeTab === 'assistant' && "Gemini FinOps Assistant"}
              {activeTab === 'planner' && "Model Pricing & Financial Planner"}
            </h1>
            <p className="main-subtitle">
              {activeTab === 'dashboard' && "Multi-tenant resource-usage logs and estimated token cost recovery metrics."}
              {activeTab === 'budgets' && "Establish cost centers, assign spending allowances, and manage proactive limits."}
              {activeTab === 'budget-consumption' && "Track live consumption against every configured budget constraint."}
              {activeTab === 'statements' && "Calendar-month chargeback statement for finance and showback reporting."}
              {activeTab === 'alerts' && "Review real-time over-budget flags and critical threshold compliance logs."}
              {activeTab === 'logging' && "Configure bigquery request-response telemetry for Gemini models on Vertex AI."}
              {activeTab === 'assistant' && "Leverage Gemini generative intelligence to query and clean up token spends."}
              {activeTab === 'planner' && "Maintain per-model token rates and build cost estimates for planned workloads."}
            </p>
          </div>

          {/* Header controls: Last Sync chip + Force Refresh button */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexShrink: 0 }}>

            {/* Last sync chip with dropdown log panel */}
            <div style={{ position: 'relative' }} ref={syncLogRef}>
              <button
                className="btn-secondary"
                onClick={() => setShowSyncLog((v) => !v)}
                aria-expanded={showSyncLog}
                aria-haspopup="listbox"
                style={{ fontSize: '12px', padding: '7px 12px', gap: '5px' }}
              >
                <span aria-live="polite" aria-atomic="true">
                  Last sync: {lastSyncAt ? formatDateTime(lastSyncAt) : '—'}
                </span>
                <span style={{ fontSize: '10px', color: 'var(--ink-3)' }}>{showSyncLog ? '▲' : '▼'}</span>
              </button>

              {showSyncLog && (
                <div
                  role="listbox"
                  aria-label="Sync log"
                  style={{
                    position: 'absolute',
                    right: 0,
                    top: 'calc(100% + 6px)',
                    background: 'var(--surface)',
                    border: '1px solid var(--line)',
                    borderRadius: 'var(--radius)',
                    boxShadow: 'var(--shadow-lg)',
                    maxHeight: '320px',
                    overflowY: 'auto',
                    minWidth: '440px',
                    zIndex: 200,
                    scrollbarWidth: 'thin',
                    scrollbarColor: 'var(--line) transparent'
                  }}
                >
                  {/* Dropdown header */}
                  <div style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    padding: '9px 14px',
                    borderBottom: '1px solid var(--line)',
                    position: 'sticky',
                    top: 0,
                    background: 'var(--surface)',
                    zIndex: 1
                  }}>
                    <span style={{ fontSize: '12px', fontWeight: '600', color: 'var(--ink-2)' }}>Sync Log</span>
                    <button
                      onClick={() => setSyncLog([])}
                      style={{
                        background: 'none',
                        border: 'none',
                        color: 'var(--ink-3)',
                        fontSize: '11px',
                        cursor: 'pointer',
                        padding: '2px 6px',
                        borderRadius: '4px',
                        transition: 'color 0.15s'
                      }}
                      onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--status-crit)'; }}
                      onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--ink-3)'; }}
                    >
                      Clear log
                    </button>
                  </div>

                  {syncLog.length === 0 ? (
                    <div style={{ padding: '20px 14px', fontSize: '12px', color: 'var(--ink-3)', textAlign: 'center' }}>
                      No syncs recorded yet.
                    </div>
                  ) : (
                    <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
                      {syncLog.map((entry, i) => (
                        <li
                          key={i}
                          role="option"
                          aria-selected="false"
                          style={{
                            padding: '10px 14px',
                            borderBottom: i < syncLog.length - 1 ? '1px solid var(--line-soft)' : 'none'
                          }}
                        >
                          <div style={{ display: 'flex', alignItems: 'center', gap: '7px', flexWrap: 'wrap' }}>
                            <span style={{
                              color: entry.ok ? 'var(--status-good)' : 'var(--status-crit)',
                              fontSize: '13px',
                              lineHeight: '1',
                              flexShrink: 0
                            }}>
                              {entry.ok ? '✓' : '✗'}
                            </span>
                            <span style={{
                              fontFamily: 'var(--font-mono)',
                              fontSize: '10.5px',
                              color: 'var(--ink-3)',
                              flexShrink: 0,
                              whiteSpace: 'nowrap'
                            }}>
                              {formatDateTime(entry.time)}
                            </span>
                            <span style={{
                              fontSize: '10px',
                              padding: '1px 6px',
                              borderRadius: '3px',
                              background: 'var(--surface-2)',
                              border: '1px solid var(--line)',
                              color: 'var(--ink-3)',
                              flexShrink: 0,
                              fontFamily: 'var(--font-mono)'
                            }}>
                              {entry.source}
                            </span>
                            <span style={{
                              fontSize: '12px',
                              color: 'var(--ink)',
                              flex: '1 1 0',
                              minWidth: 0
                            }}>
                              {entry.summary}
                            </span>
                          </div>
                          {entry.detail && (
                            <div style={{
                              marginTop: '4px',
                              marginLeft: '20px',
                              fontSize: '11px',
                              color: 'var(--status-crit)',
                              fontFamily: 'var(--font-mono)',
                              wordBreak: 'break-word'
                            }}>
                              {entry.detail}
                            </div>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </div>

            {/* Force Refresh button */}
            <button
              className="btn-secondary"
              onClick={() => refreshData('manual')}
              disabled={syncState === 'running'}
              style={{ gap: '6px' }}
              aria-label={
                syncState === 'running' ? 'Syncing…' :
                syncState === 'success' ? 'Last sync succeeded' :
                syncState === 'error'   ? 'Last sync failed' :
                'Force Refresh'
              }
            >
              {refreshButtonContent}
            </button>
          </div>
        </div>

        {/* Dismissible API error banner */}
        {apiError && (
          <div style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            backgroundColor: 'var(--status-crit-bg)',
            border: '1px solid var(--status-crit)',
            borderRadius: 'var(--radius)',
            padding: '12px 16px',
            marginBottom: '20px',
            color: 'var(--status-crit)',
            fontSize: '13px',
            fontWeight: '500',
            gap: '12px'
          }}>
            <span>⚠️ {apiError}</span>
            <button
              onClick={() => setApiError(null)}
              style={{
                background: 'none',
                border: 'none',
                color: 'var(--status-crit)',
                cursor: 'pointer',
                fontSize: '16px',
                lineHeight: '1',
                padding: '0 4px',
                flexShrink: 0
              }}
              aria-label="Dismiss error"
            >
              ✕
            </button>
          </div>
        )}

        {isLoading ? (
          <div style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            height: '400px',
            color: 'var(--ink-2)',
            fontSize: '15px',
            fontFamily: 'var(--font-mono)',
            gap: '12px'
          }}>
            <span className="assistant-pulsar" />
            <span>Loading Google Cloud Log Router and BigQuery views...</span>
          </div>
        ) : (
          <div>
            {activeTab === 'dashboard' && (
              <div>
                {/* Dynamic Filters Row */}
                <div className="filter-bar-panel" style={{
                  display: 'flex',
                  gap: '16px',
                  alignItems: 'center',
                  marginBottom: (truncated || (unpricedModels.length > 0 && dismissedUnpricedSig !== unpricedModels.join('|')) || showAnomalyBanner) ? '8px' : '20px',
                  padding: '14px 18px',
                  flexWrap: 'wrap'
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <span style={{ fontSize: '14px' }}>🔍</span>
                    <span style={{ fontSize: '13px', fontWeight: '600', color: 'var(--ink-2)' }}>Filters:</span>
                  </div>

                  <div style={{ minWidth: '240px', flex: '1 1 auto' }}>
                    <select
                      className="form-control"
                      value={selectedPrincipal}
                      onChange={(e) => setSelectedPrincipal(e.target.value)}
                      style={{ height: '38px', padding: '0 12px' }}
                    >
                      <option value="all">🌐 All Principals & Service Accounts</option>
                      {uniquePrincipals.map(p => (
                        <option key={p} value={p}>{p}</option>
                      ))}
                    </select>
                  </div>

                  <div style={{ minWidth: '200px', flex: '1 1 auto' }}>
                    <select
                      className="form-control"
                      value={selectedModel}
                      onChange={(e) => setSelectedModel(e.target.value)}
                      style={{ height: '38px', padding: '0 12px' }}
                    >
                      <option value="all">🤖 All Foundation Models</option>
                      {uniqueModels.map(m => (
                        <option key={m} value={m}>{m}</option>
                      ))}
                    </select>
                  </div>

                  {(selectedPrincipal !== 'all' || selectedModel !== 'all') && (
                    <button
                      className="btn-secondary"
                      onClick={() => { setSelectedPrincipal('all'); setSelectedModel('all'); }}
                      style={{
                        borderColor: 'var(--status-crit)',
                        color: 'var(--status-crit)',
                      }}
                    >
                      Clear Filters
                    </button>
                  )}
                </div>

                {/* Truncation warning */}
                {truncated && (
                  <div style={{
                    marginBottom: (unpricedModels.length > 0 && dismissedUnpricedSig !== unpricedModels.join('|')) || showAnomalyBanner ? '8px' : '20px',
                    padding: '8px 14px',
                    borderRadius: 'var(--radius)',
                    background: 'var(--status-warn-bg)',
                    border: '1px solid var(--status-warn-fill)',
                    fontSize: '12px',
                    color: 'var(--status-warn-text)',
                    fontWeight: '500'
                  }}>
                    ⚠ Showing the most recent 1,000 rows — totals may undercount.
                  </div>
                )}

                {/* Unpriced models notice */}
                {unpricedModels.length > 0 && dismissedUnpricedSig !== unpricedModels.join('|') && (
                  <div style={{
                    marginBottom: showAnomalyBanner ? '8px' : '20px',
                    padding: '8px 14px',
                    borderRadius: 'var(--radius)',
                    background: 'var(--status-warn-bg)',
                    border: '1px solid var(--status-warn-fill)',
                    fontSize: '12px',
                    color: 'var(--status-warn-text)',
                    fontWeight: '500',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: '12px',
                  }}>
                    <span>
                      ⚠ {unpricedModels.length} model(s) have no pricing configured — their usage shows $0:{' '}
                      {unpricedModels.join(', ')}.{' '}
                      <a
                        href="#planner"
                        onClick={(e) => { e.preventDefault(); setActiveTab('planner'); }}
                        style={{ color: 'var(--status-warn-text)', textDecoration: 'underline', cursor: 'pointer' }}
                      >
                        Add pricing →
                      </a>
                    </span>
                    <button
                      onClick={() => setDismissedUnpricedSig(unpricedModels.join('|'))}
                      style={{
                        background: 'none', border: 'none',
                        color: 'var(--status-warn-text)', cursor: 'pointer',
                        fontSize: '16px', lineHeight: '1', padding: '0 4px', flexShrink: 0,
                      }}
                      aria-label="Dismiss unpriced models notice"
                    >
                      ✕
                    </button>
                  </div>
                )}

                {showAnomalyBanner && (
                  <CostAnomalyBanner
                    anomaly={costAnomaly}
                    onDismiss={() => setDismissedAnomalyKey(anomalyKey)}
                  />
                )}
                <SummaryCards logs={dashboardRows} alerts={alerts} rangeLabel={rangeLabel} />
                <UsageCharts
                  logs={dashboardRows}
                  loggingConfig={loggingConfig}
                  chartRange={chartRange}
                  setChartRange={setChartRange}
                  chartLoading={chartLoading}
                  chartTruncated={chartTruncated}
                />
                <UserTable logs={dashboardRows} budgets={budgets} budgetStatus={budgetStatus} rangeLabel={rangeLabel} />
                <ConfigHealth
                  health={configHealth}
                  onNavigate={setActiveTab}
                  dismissed={configHealthDismissed}
                  onDismiss={handleDismissConfigHealth}
                  onRestore={handleRestoreConfigHealth}
                />
              </div>
            )}

            {activeTab === 'budgets' && (
              <BudgetManager budgets={budgets} onSaveBudgets={handleSaveBudgets} />
            )}

            {activeTab === 'statements' && (
              <MonthlyStatement />
            )}

            {activeTab === 'budget-consumption' && (
              <BudgetConsumption
                budgetStatus={budgetStatus}
                budgets={budgets}
                onNavigateToBudgets={() => setActiveTab('budgets')}
              />
            )}

            {activeTab === 'alerts' && (
              <AlertCenter alerts={alerts} />
            )}

            {activeTab === 'assistant' && (
              <FinOpsAssistant />
            )}

            {activeTab === 'logging' && (
              <ModelLogging />
            )}

            {activeTab === 'planner' && (
              <PricingPlanner unpricedModels={unpricedModels} />
            )}

          </div>
        )}
      </div>
    </div>
  );
}
