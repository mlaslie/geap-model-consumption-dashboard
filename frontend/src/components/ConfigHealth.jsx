import React from 'react';

const MAX_VISIBLE = 6;

export default function ConfigHealth({ health, onNavigate }) {
  if (!health) return null;

  const {
    unused_budgets,
    logged_unused_models,
    used_unpriced_models,
    unlogged_used_models,
    usage_window_days,
  } = health;

  const allEmpty = (
    (!unused_budgets || unused_budgets.length === 0) &&
    (!logged_unused_models || logged_unused_models.length === 0) &&
    (!used_unpriced_models || used_unpriced_models.length === 0) &&
    (!unlogged_used_models || unlogged_used_models.length === 0)
  );

  // Build rows in priority order — unlogged first (most actionable), unused-logged last (muted)
  const rows = [];

  if (unlogged_used_models && unlogged_used_models.length > 0) {
    rows.push({
      dotColor: 'var(--status-warn-fill)',
      textColor: 'var(--status-warn-text)',
      label: 'Models in use with payload logging OFF (future usage may go untracked)',
      items: unlogged_used_models,
      actionText: 'Enable logging →',
      onAction: () => onNavigate('logging'),
    });
  }

  if (used_unpriced_models && used_unpriced_models.length > 0) {
    rows.push({
      dotColor: 'var(--status-warn-fill)',
      textColor: 'var(--ink-2)',
      label: 'Models in use with no pricing configured (cost shows $0)',
      items: used_unpriced_models,
      actionText: 'Add pricing →',
      onAction: () => onNavigate('planner'),
    });
  }

  if (unused_budgets && unused_budgets.length > 0) {
    rows.push({
      dotColor: 'var(--status-warn-fill)',
      textColor: 'var(--ink-2)',
      label: `Budgets with no usage in the last ${usage_window_days} days`,
      items: unused_budgets.map(b => b.identity),
      actionText: 'Review budgets →',
      onAction: () => onNavigate('budgets'),
    });
  }

  if (logged_unused_models && logged_unused_models.length > 0) {
    rows.push({
      dotColor: 'var(--ink-3)',
      textColor: 'var(--ink-3)',
      label: 'Models logged but unused (safe to disable)',
      items: logged_unused_models,
      actionText: 'Review logging →',
      onAction: () => onNavigate('logging'),
    });
  }

  return (
    <div className="card-panel" style={{ minHeight: 'auto', marginTop: '24px' }}>
      <div className="panel-title">Configuration Health</div>

      {allEmpty ? (
        <div style={{
          fontSize: '13px',
          color: 'var(--ink-2)',
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
        }}>
          <span style={{ color: 'var(--status-good)', fontSize: '14px' }}>✓</span>
          <span>Configuration healthy — no unused budgets, unlogged models, or unpriced models detected.</span>
        </div>
      ) : (
        <div>
          {rows.map((row, i) => {
            const visible = row.items.slice(0, MAX_VISIBLE);
            const extra = row.items.length - MAX_VISIBLE;
            const isLast = i === rows.length - 1;
            return (
              <div
                key={i}
                style={{
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: '10px',
                  padding: '8px 0',
                  borderBottom: isLast ? 'none' : '1px solid var(--line-soft)',
                  color: row.textColor,
                }}
              >
                <span style={{
                  color: row.dotColor,
                  fontSize: '8px',
                  lineHeight: '1',
                  marginTop: '4px',
                  flexShrink: 0,
                }}>
                  ●
                </span>
                <div style={{ flex: 1, minWidth: 0, fontSize: '12px' }}>
                  <span>{row.label}: </span>
                  <span style={{ fontFamily: 'var(--font-mono)', wordBreak: 'break-all' }}>
                    {visible.join(', ')}{extra > 0 ? `, +${extra} more` : ''}
                  </span>
                </div>
                <button
                  onClick={row.onAction}
                  style={{
                    background: 'none',
                    border: 'none',
                    color: 'var(--accent-text)',
                    cursor: 'pointer',
                    fontSize: '11.5px',
                    fontWeight: '500',
                    padding: '0',
                    flexShrink: 0,
                    textDecoration: 'underline',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {row.actionText}
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
