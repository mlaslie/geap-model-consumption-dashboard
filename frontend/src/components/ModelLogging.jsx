import React, { useState, useEffect, useRef } from 'react';
import { apiFetch } from '../utils/api';

export default function ModelLogging() {
  const [modelStates, setModelStates] = useState({});
  const [savedConfig, setSavedConfig] = useState({});
  const [catalogModels, setCatalogModels] = useState(new Set());
  const [newModels, setNewModels] = useState(new Set());
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [autoSync, setAutoSync] = useState(false);
  const [isAutoSyncSaving, setIsAutoSyncSaving] = useState(false);
  const [notification, setNotification] = useState(null);
  const selectAllRef = useRef(null);
  // Keep a ref to current modelStates so async refresh callbacks read fresh state
  const modelStatesRef = useRef({});

  useEffect(() => {
    modelStatesRef.current = modelStates;
  }, [modelStates]);

  useEffect(() => {
    // Sequence config THEN catalog: the config fetch REPLACES modelStates, so
    // a catalog merge that raced ahead of it would be clobbered (models from
    // the catalog would vanish until Refresh was clicked).
    fetchLoggingConfig().then(() => fetchCatalog(false));
    fetchAutoSyncConfig();
  }, []); // intentional mount-only

  // ── Notification helper ───────────────────────────────────────────────────
  const showNotification = (message, type = 'success') => {
    setNotification({ message, type });
    setTimeout(() => setNotification(null), 5000);
  };

  // ── Logging config (existing GET / POST) ──────────────────────────────────
  const fetchLoggingConfig = async () => {
    setIsLoading(true);
    try {
      const response = await apiFetch('/api/logging-config');
      const json = await response.json();
      if (!response.ok) {
        showNotification(
          `Failed to load configuration: ${json.detail || response.statusText}`,
          'error'
        );
      } else if (json.status === 'success') {
        setModelStates(json.data);
        setSavedConfig(json.data);
      }
    } catch (e) {
      if (e.message !== 'Unauthorized') {
        console.error('Failed to load model logging configs:', e);
        showNotification('Failed to load configuration', 'error');
      }
    } finally {
      setIsLoading(false);
    }
  };

  // ── Available-models catalog ──────────────────────────────────────────────
  // BOTH paths merge catalog models into the displayed list (new ones
  // unchecked, badged "new") so every available model shows on page load —
  // not only after clicking Refresh. Differences:
  //   force=false (mount) → served from the backend's 5-min cache; silent.
  //   force=true (button) → fresh API fetch; shows the found/new notification.
  const fetchCatalog = async (force) => {
    const url = force ? '/api/available-models?force=true' : '/api/available-models';
    if (force) setIsRefreshing(true);
    try {
      const response = await apiFetch(url);
      const json = await response.json();
      if (!response.ok) {
        if (force) {
          const msg =
            response.status === 503
              ? `Model catalog unreachable: ${json.detail || 'Service unavailable'}`
              : `Failed to fetch available models: ${json.detail || response.statusText}`;
          showNotification(msg, 'error');
        }
        return;
      }
      if (json.status === 'success') {
        const models = json.data.models || [];
        const catalogSet = new Set(models);
        setCatalogModels(catalogSet);

        // Merge on BOTH paths so available models appear on page load.
        // Read current state via ref so we don't get a stale closure value.
        const current = modelStatesRef.current;
        const merged = { ...current };
        const added = new Set();
        for (const modelId of models) {
          if (!(modelId in merged)) {
            merged[modelId] = false;
            added.add(modelId);
          }
        }
        if (added.size > 0) {
          setModelStates(merged);
          setNewModels(prev => new Set([...prev, ...added]));
        }
        if (force) {
          showNotification(
            `Found ${models.length} available models — ${added.size} new`
          );
        }
      }
    } catch (e) {
      if (e.message !== 'Unauthorized') {
        console.error('Failed to fetch available models:', e);
        if (force) showNotification('Failed to fetch available models', 'error');
      }
    } finally {
      if (force) setIsRefreshing(false);
    }
  };

  // ── Auto-sync config ──────────────────────────────────────────────────────
  const fetchAutoSyncConfig = async () => {
    try {
      const response = await apiFetch('/api/model-sync-config');
      const json = await response.json();
      if (response.ok && json.status === 'success') {
        setAutoSync(json.data.auto_sync_on_startup);
      }
    } catch (e) {
      if (e.message !== 'Unauthorized') {
        console.error('Failed to load auto-sync config:', e);
      }
    }
  };

  const handleAutoSyncToggle = async () => {
    const newValue = !autoSync;
    setAutoSync(newValue); // optimistic
    setIsAutoSyncSaving(true);
    try {
      const response = await apiFetch('/api/model-sync-config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ auto_sync_on_startup: newValue }),
      });
      const json = await response.json();
      if (!response.ok || json.status !== 'success') {
        setAutoSync(!newValue); // revert
        showNotification(
          `Failed to save auto-sync setting: ${json.detail || response.statusText}`,
          'error'
        );
      } else {
        showNotification(newValue ? 'Auto-sync enabled' : 'Auto-sync disabled');
      }
    } catch (e) {
      setAutoSync(!newValue); // revert
      if (e.message !== 'Unauthorized') {
        console.error('Failed to save auto-sync config:', e);
        showNotification('Failed to save auto-sync setting', 'error');
      }
    } finally {
      setIsAutoSyncSaving(false);
    }
  };

  // ── Model checkbox handlers ───────────────────────────────────────────────
  const handleCheckboxChange = (modelId) => {
    setModelStates(prev => ({ ...prev, [modelId]: !prev[modelId] }));
  };

  const handleSelectAll = (e) => {
    const checked = e.target.checked;
    setModelStates(prev => {
      const updated = {};
      for (const key of Object.keys(prev)) {
        updated[key] = checked;
      }
      return updated;
    });
  };

  // ── Apply (POST logging-config) ───────────────────────────────────────────
  const handleApply = async () => {
    setIsSaving(true);
    try {
      const response = await apiFetch('/api/logging-config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(modelStates),
      });
      const json = await response.json();
      if (!response.ok) {
        showNotification(
          `Failed to save config: ${json.detail || response.statusText}`,
          'error'
        );
      } else if (json.status === 'success') {
        setSavedConfig({ ...modelStates });
        setNewModels(new Set()); // clear "new" badges after a successful save
        showNotification('Request-response logging successfully updated!');
      } else if (json.status === 'partial_failure') {
        const failed = Object.entries(json.results || {})
          .filter(([, r]) => !r.success)
          .map(([modelId, r]) => {
            const errMsg = r.error ? r.error.slice(0, 80) : 'unknown error';
            return `${modelId}: ${errMsg}`;
          });
        showNotification(`Partial failure — ${failed.join('; ')}`, 'error');
      } else {
        showNotification(
          json.message || json.detail || 'Failed to save config',
          'error'
        );
      }
    } catch (e) {
      if (e.message !== 'Unauthorized') {
        console.error('Failed to update logging configurations:', e);
        showNotification('Error connecting to Vertex AI SDK', 'error');
      }
    } finally {
      setIsSaving(false);
    }
  };

  // ── Derived state ─────────────────────────────────────────────────────────
  const modelIds = Object.keys(modelStates);
  const checkedCount = modelIds.filter(id => modelStates[id]).length;
  const allChecked = modelIds.length > 0 && checkedCount === modelIds.length;
  const someChecked = checkedCount > 0 && checkedCount < modelIds.length;

  // Drive the native indeterminate attribute via a ref
  useEffect(() => {
    if (selectAllRef.current) {
      selectAllRef.current.indeterminate = someChecked;
    }
  }, [someChecked]);

  // Cross-key comparison — handles new models added via refresh (missing in savedConfig)
  const hasUnsavedChanges = (() => {
    const allKeys = new Set([
      ...Object.keys(modelStates),
      ...Object.keys(savedConfig),
    ]);
    for (const k of allKeys) {
      if (modelStates[k] !== savedConfig[k]) return true;
    }
    return false;
  })();

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="card-container" style={{ animation: 'fadeIn 0.5s ease-out' }}>

      {/* Header row: title + refresh button */}
      <div className="card-header" style={{ marginBottom: '24px' }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '16px' }}>
          <div>
            <h2 style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span>📡</span> Vertex AI Foundation Model Payload Logging
            </h2>
            <p style={{ color: 'var(--text-secondary)', fontSize: '13px', marginTop: '6px' }}>
              Dynamically toggle payload request-response logging directly into your BigQuery dataset{' '}
              <code style={{ fontFamily: 'var(--font-mono)', fontSize: '12px' }}>vertex_ai_user_telemetry</code>{' '}
              via the Vertex AI SDK.
            </p>
          </div>
          <button
            className="btn-secondary"
            onClick={() => fetchCatalog(true)}
            disabled={isRefreshing || isLoading}
            style={{ flexShrink: 0, marginTop: '2px' }}
          >
            {isRefreshing ? (
              <>
                <span className="spin-motion">↻</span>
                <span className="spin-reduced">…</span>
                {' '}Refreshing...
              </>
            ) : (
              '↻ Refresh model list'
            )}
          </button>
        </div>
      </div>

      {/* Notification banner */}
      {notification && (
        <div style={{
          backgroundColor: notification.type === 'success'
            ? 'var(--accent-emerald-glow)'
            : 'var(--status-crit-bg)',
          border: `1px solid ${notification.type === 'success' ? 'var(--series-3)' : 'var(--status-crit)'}`,
          borderRadius: 'var(--radius)',
          padding: '12px 16px',
          color: notification.type === 'success' ? 'var(--status-good)' : 'var(--status-crit)',
          fontSize: '13px',
          fontWeight: '500',
          marginBottom: '20px',
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
        }}>
          <span>{notification.type === 'success' ? '✅' : '⚠️'}</span>
          <span>{notification.message}</span>
        </div>
      )}

      {isLoading ? (
        <div style={{ padding: '24px 0', textAlign: 'center', color: 'var(--text-secondary)' }}>
          Loading Vertex foundation model statuses...
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>

          {/* Model list panel */}
          <div style={{
            backgroundColor: 'var(--surface-2)',
            borderRadius: 'var(--radius)',
            border: '1px solid var(--line)',
            overflow: 'hidden',
          }}>
            {/* Select-all header row */}
            <label style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              padding: '10px 20px',
              backgroundColor: 'var(--plane)',
              borderBottom: '1px solid var(--line)',
              cursor: 'pointer',
            }}>
              <span style={{
                fontSize: '11.5px',
                fontWeight: '700',
                color: 'var(--ink-2)',
                textTransform: 'uppercase',
                letterSpacing: '0.07em',
              }}>
                Select all
              </span>
              <input
                ref={selectAllRef}
                type="checkbox"
                checked={allChecked}
                onChange={handleSelectAll}
                style={{
                  width: '16px',
                  height: '16px',
                  accentColor: 'var(--accent)',
                  cursor: 'pointer',
                }}
              />
            </label>

            {/* Per-model rows */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', padding: '12px' }}>
              {modelIds.map((modelId) => {
                const isNew = newModels.has(modelId);
                const notInCatalog = catalogModels.size > 0 && !catalogModels.has(modelId);
                return (
                  <label
                    key={modelId}
                    className="hover-bright"
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      padding: '12px 16px',
                      borderRadius: '8px',
                      backgroundColor: 'var(--plane)',
                      border: '1px solid var(--line)',
                      cursor: 'pointer',
                      transition: 'all 0.2s ease',
                    }}
                  >
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <span style={{
                          fontSize: '14px',
                          fontWeight: '600',
                          color: 'var(--ink)',
                          fontFamily: 'var(--font-mono)',
                        }}>
                          {modelId}
                        </span>
                        {isNew && (
                          <span style={{
                            fontSize: '10px',
                            fontWeight: '700',
                            padding: '1px 6px',
                            borderRadius: '3px',
                            backgroundColor: 'var(--accent-soft)',
                            color: 'var(--accent-text, var(--accent))',
                            border: '1px solid var(--accent)',
                            letterSpacing: '0.03em',
                            lineHeight: '1.6',
                          }}>
                            new
                          </span>
                        )}
                        {notInCatalog && (
                          <span style={{
                            fontSize: '10px',
                            fontWeight: '600',
                            padding: '1px 6px',
                            borderRadius: '3px',
                            backgroundColor: 'var(--surface-2)',
                            color: 'var(--ink-3)',
                            border: '1px solid var(--line)',
                            letterSpacing: '0.03em',
                            lineHeight: '1.6',
                          }}>
                            not in catalog
                          </span>
                        )}
                      </div>
                      <span style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>
                        Foundation model deployed in global region
                      </span>
                    </div>
                    <input
                      type="checkbox"
                      checked={modelStates[modelId]}
                      onChange={() => handleCheckboxChange(modelId)}
                      style={{
                        width: '18px',
                        height: '18px',
                        accentColor: 'var(--accent-teal)',
                        cursor: 'pointer',
                        flexShrink: 0,
                      }}
                    />
                  </label>
                );
              })}
            </div>
          </div>

          {/* How-it-works callout */}
          <div style={{
            backgroundColor: 'var(--accent-soft)',
            border: '1px solid var(--accent)',
            borderRadius: 'var(--radius)',
            padding: '12px 16px',
            fontSize: '12px',
            color: 'var(--accent)',
            lineHeight: '1.5',
          }}>
            <strong>💡 How this works:</strong> Enabling logging calls the preview{' '}
            <code style={{ fontFamily: 'var(--font-mono)', fontSize: '11px' }}>
              set_request_response_logging_config
            </code>{' '}
            function in the Vertex AI SDK. Raw input prompts, generated responses, and exact token counts are routed
            directly into the{' '}
            <code style={{ fontFamily: 'var(--font-mono)', fontSize: '11px' }}>request_response_logging</code>{' '}
            table in BigQuery, making them instantly available in the dashboard!
          </div>

          {/* Apply row with unsaved-changes hint */}
          <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: '12px' }}>
            {hasUnsavedChanges && (
              <span style={{ fontSize: '12px', color: 'var(--ink-3)', fontStyle: 'italic' }}>
                Unsaved changes — click Apply
              </span>
            )}
            <button
              className="btn-primary"
              onClick={handleApply}
              disabled={isSaving}
              style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '10px 24px' }}
            >
              {isSaving ? (
                <>
                  <span className="assistant-pulsar" style={{ width: '12px', height: '12px' }} />
                  Applying configs...
                </>
              ) : (
                'Apply Logging Settings'
              )}
            </button>
          </div>

          {/* Auto-sync settings block */}
          <div style={{
            border: '1px solid var(--line)',
            borderRadius: 'var(--radius)',
            padding: '18px 20px',
            backgroundColor: 'var(--surface-2)',
          }}>
            <div style={{
              fontSize: '11px',
              fontWeight: '700',
              textTransform: 'uppercase',
              letterSpacing: '0.09em',
              color: 'var(--ink-3)',
              marginBottom: '14px',
            }}>
              Startup Settings
            </div>
            <label style={{
              display: 'flex',
              alignItems: 'flex-start',
              gap: '12px',
              cursor: isAutoSyncSaving ? 'wait' : 'pointer',
            }}>
              <input
                type="checkbox"
                checked={autoSync}
                onChange={handleAutoSyncToggle}
                disabled={isAutoSyncSaving}
                style={{
                  width: '16px',
                  height: '16px',
                  accentColor: 'var(--accent)',
                  cursor: isAutoSyncSaving ? 'wait' : 'pointer',
                  marginTop: '3px',
                  flexShrink: 0,
                }}
              />
              <div>
                <div style={{
                  fontSize: '13.5px',
                  fontWeight: '600',
                  color: 'var(--ink)',
                  marginBottom: '5px',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                }}>
                  Auto-sync available models on launch
                  {isAutoSyncSaving && (
                    <span style={{ fontSize: '11px', color: 'var(--ink-3)', fontWeight: '400' }}>
                      saving…
                    </span>
                  )}
                </div>
                <p style={{ fontSize: '12px', color: 'var(--ink-2)', lineHeight: '1.55', margin: 0 }}>
                  On application startup, newly available Gemini models are discovered automatically, enabled for payload
                  logging, and applied to Vertex AI — zero-ops support for new model releases. New models still need
                  pricing added in <strong>Pricing &amp; Planner</strong> to contribute costs.
                </p>
              </div>
            </label>
          </div>

        </div>
      )}
    </div>
  );
}
