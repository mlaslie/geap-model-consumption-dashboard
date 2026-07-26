import React, { useState } from 'react';
import { formatCurrency, formatTokens } from '../utils/formatters';

// Sortable column definitions — key, label, default sort direction on first click.
const COLUMNS = [
  { key: 'principal',   label: 'Principal',     defaultDir: 'asc'  },
  { key: 'budgetType',  label: 'Budget Type',   defaultDir: 'asc'  },
  { key: 'period',      label: 'Period',        defaultDir: 'asc'  },
  { key: 'limit',       label: 'Limit',         defaultDir: 'desc' },
  { key: 'consumed',    label: 'Consumed',      defaultDir: 'desc' },
  { key: 'percentage',  label: '% of Budget',   defaultDir: 'desc' },
  { key: 'status',      label: 'Status',        defaultDir: 'desc' },
];

const PERIOD_LABELS = { day: 'Daily', week: 'Weekly', month: 'Monthly', year: 'Yearly' };

// Numeric severity used for Status column sort (higher = worse).
const statusSeverity = (pct, threshold) => {
  if (pct >= 100) return 2;
  if (pct >= threshold) return 1;
  return 0;
};

const isServiceAccount = (identity) =>
  identity.includes('.gserviceaccount.com') || identity.includes('service-account');

export default function BudgetConsumption({ budgetStatus, budgets: _budgets, onNavigateToBudgets }) {
  const [searchTerm, setSearchTerm] = useState('');
  const [sortCol, setSortCol] = useState('principal');
  const [sortDir, setSortDir] = useState('asc');

  const handleSort = (key) => {
    if (key === sortCol) {
      setSortDir(sortDir === 'asc' ? 'desc' : 'asc');
    } else {
      setSortCol(key);
      setSortDir(COLUMNS.find(c => c.key === key)?.defaultDir || 'asc');
    }
  };

  // Build a flat row list from budgetStatus.
  const statusEntries = budgetStatus ? Object.entries(budgetStatus) : [];

  // ── Empty state ────────────────────────────────────────────────────────────
  if (statusEntries.length === 0) {
    return (
      <div className="table-panel">
        <div className="bc-page-header">
          <div>
            <h2 className="bc-title">Consumption vs. Configured Constraints</h2>
            <p className="bc-subtitle">
              Live consumption measured against each principal's configured budget period.
            </p>
          </div>
        </div>
        <div className="bc-empty-state">
          <p className="bc-empty-msg">No budget consumption data yet.</p>
          <button type="button" className="btn-primary" onClick={onNavigateToBudgets}>
            Configure budgets →
          </button>
        </div>
      </div>
    );
  }

  // ── Summary counts ─────────────────────────────────────────────────────────
  let countOver = 0;
  let countApproaching = 0;
  let countWithin = 0;
  let anyHardLimit = false;

  statusEntries.forEach(([, s]) => {
    const pct = s.percentage ?? 0;
    const threshold = s.threshold_percentage ?? 50;
    if (pct >= 100) countOver++;
    else if (pct >= threshold) countApproaching++;
    else countWithin++;
    if (s.hard_limit_enabled) anyHardLimit = true;
  });

  const totalPrincipals = statusEntries.length;

  // ── Row construction ───────────────────────────────────────────────────────
  const term = searchTerm.toLowerCase();
  const dir = sortDir === 'asc' ? 1 : -1;

  const rows = statusEntries
    .map(([identity, s], originalIndex) => ({
      identity,
      pct: s.percentage ?? 0,
      threshold: s.threshold_percentage ?? 50,
      type: s.type,
      period: s.period,
      limit: s.limit,
      consumed: s.consumed,
      hardLimitEnabled: s.hard_limit_enabled,
      isGlobalDefault: s.is_global_default,
      _idx: originalIndex, // stable sort tiebreaker
    }))
    .filter(r => r.identity.toLowerCase().includes(term));

  rows.sort((a, b) => {
    let cmp = 0;
    switch (sortCol) {
      case 'principal':
        cmp = a.identity.localeCompare(b.identity);
        break;
      case 'budgetType':
        cmp = a.type.localeCompare(b.type);
        break;
      case 'period': {
        const order = { day: 0, week: 1, month: 2, year: 3 };
        cmp = (order[a.period] ?? 99) - (order[b.period] ?? 99);
        break;
      }
      case 'limit':
        cmp = a.limit - b.limit;
        break;
      case 'consumed':
        cmp = a.consumed - b.consumed;
        break;
      case 'percentage':
        cmp = a.pct - b.pct;
        break;
      case 'status':
        cmp = statusSeverity(a.pct, a.threshold) - statusSeverity(b.pct, b.threshold);
        break;
      default:
        cmp = 0;
    }
    if (cmp !== 0) return dir * cmp;
    // Stable tiebreaker
    return a._idx - b._idx;
  });

  return (
    <div className="table-panel">
      {/* ── Page header ────────────────────────────────────────────────────── */}
      <div className="bc-page-header">
        <div>
          <h2 className="bc-title">Consumption vs. Configured Constraints</h2>
          <p className="bc-subtitle">
            Live consumption measured against each principal's configured budget period.
          </p>
        </div>
        <p className="bc-period-note">
          Each row is scoped to that principal's own budget period — not a fixed window.
        </p>
      </div>

      {/* ── Summary tiles ──────────────────────────────────────────────────── */}
      <div className="bc-summary-strip">
        <div className="bc-stat-tile bc-stat-crit">
          <span className="bc-stat-value">{countOver}</span>
          <span className="bc-stat-label">Over Limit</span>
        </div>
        <div className="bc-stat-tile bc-stat-warn">
          <span className="bc-stat-value">{countApproaching}</span>
          <span className="bc-stat-label">Approaching</span>
        </div>
        <div className="bc-stat-tile bc-stat-good">
          <span className="bc-stat-value">{countWithin}</span>
          <span className="bc-stat-label">Within Budget</span>
        </div>
        <div className="bc-stat-tile bc-stat-neutral">
          <span className="bc-stat-value">{totalPrincipals}</span>
          <span className="bc-stat-label">Total Principals</span>
        </div>
      </div>

      {/* ── Table header row ───────────────────────────────────────────────── */}
      <div className="table-header-row">
        <h3 style={{ fontSize: '15px', fontWeight: '600', color: 'var(--ink)' }}>
          Per-Principal Budget Status
        </h3>
        <input
          type="text"
          placeholder="Filter by principal..."
          className="table-search-input"
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
        />
      </div>

      {/* ── Table ──────────────────────────────────────────────────────────── */}
      <div className="custom-table-container">
        <table className="custom-table">
          <thead>
            <tr>
              {COLUMNS.map(col => (
                <th
                  key={col.key}
                  aria-sort={
                    sortCol === col.key
                      ? sortDir === 'asc' ? 'ascending' : 'descending'
                      : 'none'
                  }
                >
                  <button
                    type="button"
                    className="th-sort-btn"
                    onClick={() => handleSort(col.key)}
                    title={`Sort by ${col.label}`}
                  >
                    {col.label}
                    <span className="th-sort-indicator" aria-hidden="true">
                      {sortCol === col.key ? (sortDir === 'asc' ? '▲' : '▼') : '↕'}
                    </span>
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td
                  colSpan={COLUMNS.length}
                  style={{ textAlign: 'center', color: 'var(--text-muted)', padding: '30px' }}
                >
                  No matching principals found.
                </td>
              </tr>
            ) : (
              rows.map(r => {
                let progressClass = 'safe';
                if (r.pct >= 100) progressClass = 'danger';
                else if (r.pct >= r.threshold) progressClass = 'warning';

                const severity = statusSeverity(r.pct, r.threshold);
                const statusLabel  = severity === 2 ? 'OVER LIMIT' : severity === 1 ? 'APPROACHING' : 'OK';
                const statusCls    = severity === 2 ? 'bc-status-pill bc-pill-crit'
                                   : severity === 1 ? 'bc-status-pill bc-pill-warn'
                                   :                  'bc-status-pill bc-pill-good';

                const formattedLimit    = r.type === 'money' ? formatCurrency(r.limit)    : formatTokens(r.limit);
                const formattedConsumed = r.type === 'money' ? formatCurrency(r.consumed) : formatTokens(r.consumed);

                return (
                  <tr key={r.identity}>
                    {/* Principal */}
                    <td className="identity-cell">
                      <span className="identity-email">{r.identity}</span>
                      {isServiceAccount(r.identity) && (
                        <span className="identity-sa-badge">SVC</span>
                      )}
                      {r.isGlobalDefault && (
                        <span className="bc-global-tag">global default rule</span>
                      )}
                    </td>

                    {/* Budget Type */}
                    <td style={{ color: 'var(--ink-2)', fontSize: '12px' }}>
                      {r.type === 'money' ? 'Money (USD)' : 'Tokens'}
                    </td>

                    {/* Period */}
                    <td style={{ color: 'var(--ink-2)', fontSize: '12px' }}>
                      {PERIOD_LABELS[r.period] ?? r.period}
                    </td>

                    {/* Limit */}
                    <td style={{ fontFamily: 'var(--font-mono)', fontSize: '12px' }}>
                      {formattedLimit}
                    </td>

                    {/* Consumed */}
                    <td style={{ fontFamily: 'var(--font-mono)', fontSize: '12px' }}>
                      {formattedConsumed}
                    </td>

                    {/* % of Budget — primary column with progress bar */}
                    <td>
                      <div className="budget-progress-container" style={{ width: '160px' }}>
                        <div className="budget-progress-bar">
                          <div
                            className={`budget-progress-fill ${progressClass}`}
                            style={{ width: `${Math.min(r.pct, 100)}%` }}
                          />
                        </div>
                        <span className="budget-progress-text">
                          {r.pct.toFixed(1)}%
                        </span>
                      </div>
                    </td>

                    {/* Status pill */}
                    <td>
                      <span className={statusCls}>{statusLabel}</span>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {/* ── Hard-limit footnote ─────────────────────────────────────────────── */}
      {anyHardLimit && (
        <p className="bc-footnote">
          Hard-limit flags are advisory — enforcement is not implemented.
        </p>
      )}
    </div>
  );
}
