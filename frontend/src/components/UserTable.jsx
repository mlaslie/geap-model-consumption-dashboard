import React, { useState } from 'react';
import { formatCurrency, formatTokens, getModelBadgeClass } from '../utils/formatters';
import { toCsv, downloadCsv, csvTimestamp } from '../utils/csv';

// Sortable columns: key, header label, default direction on first click, and
// whether the header cell is center-aligned.
const COLUMNS = [
  { key: 'principal', label: 'Principal Email', defaultDir: 'asc' },
  { key: 'model', label: 'Model', defaultDir: 'asc' },
  { key: 'calls', label: 'Calls', defaultDir: 'desc', center: true },
  { key: 'tokens', label: 'Tokens In / Out', defaultDir: 'desc' },
  { key: 'cost', label: 'Cost (USD)', defaultDir: 'desc' },
  { key: 'budget', label: 'Budget Consumption', defaultDir: 'desc' },
];

export default function UserTable({ logs, budgets, budgetStatus, rangeLabel = 'Last 30 days' }) {
  const [searchTerm, setSearchTerm] = useState('');
  // Default order matches prior behavior: principals A→Z (with each user's
  // models by token volume as the tie-break).
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

  // Aggregate one row per (user, model) pair. A user who has used three
  // models appears as three rows, each with that model's token counts.
  const pairAggregation = {};
  logs.forEach(log => {
    const user = log.user_email || 'unknown_user';
    const model = log.model_name || 'unknown';
    // '|' cannot appear in an email address or a validated model id, so the
    // composite key cannot collide across different (user, model) pairs.
    const key = `${user}|${model}`;
    if (!pairAggregation[key]) {
      pairAggregation[key] = {
        email: user,
        model,
        calls: 0,
        inputTokens: 0,
        outputTokens: 0,
        totalTokens: 0,
        cost: 0.0,
        hasDefaultPricing: false,
        hasPricedLogs: false,
      };
    }

    const row = pairAggregation[key];
    // Use true call count from daily-grain rows when available; fall back to 1 per row
    row.calls += (log.call_count != null ? log.call_count : 1);
    row.inputTokens += log.input_tokens || 0;
    row.outputTokens += log.output_tokens || 0;
    row.totalTokens += log.total_tokens || 0;
    row.cost += log.estimated_cost_usd || 0;
    if (log.pricing_match === 'default') {
      row.hasDefaultPricing = true;
    } else {
      row.hasPricedLogs = true;
    }
  });

  // Per-user totals (budgets apply to the PRINCIPAL across all models) —
  // needed both for the fallback budget computation and for budget sorting.
  const userTotals = {};
  Object.values(pairAggregation).forEach(r => {
    if (!userTotals[r.email]) userTotals[r.email] = { cost: 0, tokens: 0 };
    userTotals[r.email].cost += r.cost;
    userTotals[r.email].tokens += r.totalTokens;
  });

  // Resolve each user's budget position once (period-aware /api/budget-status
  // preferred; local fallback otherwise). Used by rendering AND sorting.
  const budgetInfoFor = (email) => {
    const statusEntry = budgetStatus && budgetStatus[email];
    if (statusEntry) {
      return {
        pct: statusEntry.percentage ?? 0,
        limit: statusEntry.limit,
        bType: statusEntry.type,
        threshold: statusEntry.threshold_percentage ?? 50,
        periodLabel: statusEntry.period ? ` / ${statusEntry.period}` : '',
      };
    }
    const budgetRule = budgets[email] || budgets["global_default"] || {
      limit: 10000000,
      type: 'token',
      alert_threshold_percentage: 50.0
    };
    const totals = userTotals[email] || { cost: 0, tokens: 0 };
    const consumed = budgetRule.type === 'money' ? totals.cost : totals.tokens;
    return {
      pct: budgetRule.limit > 0 ? (consumed / budgetRule.limit) * 100 : 0,
      limit: budgetRule.limit,
      bType: budgetRule.type,
      threshold: budgetRule.alert_threshold_percentage,
      periodLabel: '',
    };
  };

  const term = searchTerm.toLowerCase();
  const dir = sortDir === 'asc' ? 1 : -1;
  const compare = (a, b) => {
    switch (sortCol) {
      case 'model':  return dir * a.model.localeCompare(b.model);
      case 'calls':  return dir * (a.calls - b.calls);
      case 'tokens': return dir * ((a.inputTokens + a.outputTokens) - (b.inputTokens + b.outputTokens));
      case 'cost':   return dir * (a.cost - b.cost);
      case 'budget': {
        // Budget position is a per-user value: order user GROUPS by pct while
        // keeping each user's rows together (tokens desc within the group).
        const pa = budgetInfoFor(a.email).pct;
        const pb = budgetInfoFor(b.email).pct;
        if (pa !== pb) return dir * (pa - pb);
        if (a.email !== b.email) return a.email.localeCompare(b.email);
        return b.totalTokens - a.totalTokens;
      }
      case 'principal':
      default:
        if (a.email !== b.email) return dir * a.email.localeCompare(b.email);
        return b.totalTokens - a.totalTokens;
    }
  };

  const rowList = Object.values(pairAggregation)
    .filter(r => r.email.toLowerCase().includes(term) || r.model.toLowerCase().includes(term))
    .sort(compare);

  // Budget bar renders only at each user's FIRST occurrence in the current
  // order. Adjacent-row comparison is not enough: non-grouped sorts (calls,
  // cost, tokens) can split a user's rows, which would repeat the bar at
  // every adjacency break.
  const firstIndexByEmail = new Map();
  rowList.forEach((r, i) => {
    if (!firstIndexByEmail.has(r.email)) firstIndexByEmail.set(r.email, i);
  });

  // Budget values needed for CSV — raw period + numeric limit/pct (not formatted).
  const csvBudgetFor = (email) => {
    const statusEntry = budgetStatus && budgetStatus[email];
    if (statusEntry) {
      return {
        period: statusEntry.period ?? '',
        limit: statusEntry.limit,
        pct: statusEntry.percentage ?? 0,
      };
    }
    const budgetRule = budgets[email] || budgets["global_default"] || {
      limit: 10000000,
      type: 'token',
    };
    const totals = userTotals[email] || { cost: 0, tokens: 0 };
    const consumed = budgetRule.type === 'money' ? totals.cost : totals.tokens;
    return {
      period: budgetRule.period ?? '',
      limit: budgetRule.limit,
      pct: budgetRule.limit > 0 ? (consumed / budgetRule.limit) * 100 : 0,
    };
  };

  const CSV_COLUMNS = [
    { key: 'principal_email', header: 'principal_email' },
    { key: 'model',           header: 'model' },
    { key: 'calls',           header: 'calls' },
    { key: 'input_tokens',    header: 'input_tokens' },
    { key: 'output_tokens',   header: 'output_tokens' },
    { key: 'total_tokens',    header: 'total_tokens' },
    { key: 'cost_usd',        header: 'cost_usd' },
    { key: 'pricing_status',  header: 'pricing_status' },
    { key: 'budget_period',   header: 'budget_period' },
    { key: 'budget_limit',    header: 'budget_limit' },
    { key: 'budget_percent',  header: 'budget_percent' },
  ];

  const handleExport = () => {
    if (rowList.length === 0) return;
    const csvRows = rowList.map(r => {
      const { period, limit, pct } = csvBudgetFor(r.email);
      const unpriced = r.hasDefaultPricing && !r.hasPricedLogs;
      return {
        principal_email: r.email,
        model:           r.model,
        calls:           r.calls,
        input_tokens:    r.inputTokens,
        output_tokens:   r.outputTokens,
        total_tokens:    r.totalTokens,
        cost_usd:        unpriced ? null : r.cost,
        pricing_status:  unpriced ? 'unpriced' : 'priced',
        budget_period:   period,
        budget_limit:    limit,
        budget_percent:  pct,
      };
    });
    const slug = rangeLabel.toLowerCase().replace(/\s+/g, '-');
    downloadCsv(`token-consumption-${slug}-${csvTimestamp()}.csv`, toCsv(CSV_COLUMNS, csvRows));
  };

  // Helper to check if email is a Service Account
  const isServiceAccount = (email) => {
    return email.includes('.gserviceaccount.com') || email.includes('service-account');
  };

  return (
    <div className="table-panel">
      <div className="table-header-row">
        <div>
          <h3 style={{fontSize: '18px', fontWeight: '600'}}>Per-Principal Token Consumption</h3>
          {/* Usage columns follow the dashboard range selector; the budget
              column is scoped to each principal's own budget period instead. */}
          <div style={{fontSize: '11px', color: 'var(--ink-3)', marginTop: '2px'}}>
            Usage columns: {rangeLabel.toLowerCase()} · Budget column: each principal's budget period
          </div>
        </div>
        <div className="table-actions">
          <input
            type="text"
            placeholder="Filter by principal or model..."
            className="table-search-input"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
          />
          <button
            type="button"
            className="btn-secondary"
            onClick={handleExport}
            disabled={rowList.length === 0}
            title={rowList.length === 0 ? 'No rows to export' : `Export ${rowList.length} rows as CSV`}
          >
            ⤓ Export CSV
          </button>
        </div>
      </div>

      <div className="custom-table-container">
        <table className="custom-table">
          <thead>
            <tr>
              {COLUMNS.map(col => (
                <th
                  key={col.key}
                  style={col.center ? { textAlign: 'center' } : undefined}
                  aria-sort={sortCol === col.key ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}
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
            {rowList.length === 0 ? (
              <tr>
                <td colSpan="6" style={{textAlign: 'center', color: 'var(--text-muted)', padding: '30px'}}>
                  No matching principals found.
                </td>
              </tr>
            ) : (
              rowList.map((r, idx) => {
                // The budget applies to the PRINCIPAL (all models combined):
                // show it only at the user's first occurrence in the current order.
                const firstRowForUser = firstIndexByEmail.get(r.email) === idx;
                const { pct, limit, bType, threshold, periodLabel } = budgetInfoFor(r.email);

                // Progress Bar Color Status
                let progressClass = 'safe';
                if (pct >= 100) progressClass = 'danger';
                else if (pct >= threshold) progressClass = 'warning';

                return (
                  <tr key={`${r.email}|${r.model}`}>
                    <td className="identity-cell">
                      <span className="identity-email">{r.email}</span>
                      {isServiceAccount(r.email) && <span className="identity-sa-badge">SVC</span>}
                    </td>
                    <td>
                      <span className={`badge-model ${getModelBadgeClass(r.model)}`}>
                        {r.model}
                      </span>
                    </td>
                    <td style={{textAlign: 'center', fontFamily: 'var(--font-mono)'}}>{r.calls}</td>
                    <td style={{fontFamily: 'var(--font-mono)', fontSize: '12px'}}>
                      <span style={{color: 'var(--text-secondary)'}}>{formatTokens(r.inputTokens)}</span>
                      <span style={{color: 'var(--text-muted)'}}> / </span>
                      <span style={{color: 'var(--text-muted)'}}>{formatTokens(r.outputTokens)}</span>
                    </td>
                    <td
                      style={{
                        fontFamily: 'var(--font-mono)',
                        fontWeight: '500',
                        color: (r.hasDefaultPricing && !r.hasPricedLogs) ? 'var(--status-warn-text)' : undefined,
                      }}
                      title={
                        (r.hasDefaultPricing && !r.hasPricedLogs)
                          ? 'No pricing configured for this model — add it in Pricing & Planner'
                          : (r.hasDefaultPricing && r.hasPricedLogs)
                          ? 'Includes unpriced usage — configure this model in Pricing & Planner'
                          : undefined
                      }
                    >
                      {(r.hasDefaultPricing && !r.hasPricedLogs)
                        ? 'unpriced'
                        : (r.hasDefaultPricing && r.hasPricedLogs)
                        ? `~${formatCurrency(r.cost)}`
                        : formatCurrency(r.cost)}
                    </td>
                    <td>
                      {firstRowForUser ? (
                        <div className="budget-progress-container">
                          <div className="budget-progress-bar">
                            <div
                              className={`budget-progress-fill ${progressClass}`}
                              style={{width: `${Math.min(pct, 100)}%`}}
                            />
                          </div>
                          <span className="budget-progress-text">
                            {pct.toFixed(1)}% of {bType === 'money' ? formatCurrency(limit) : formatTokens(limit)}{periodLabel}
                          </span>
                        </div>
                      ) : (
                        <span style={{color: 'var(--text-muted)', fontSize: '11px'}}>—</span>
                      )}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
