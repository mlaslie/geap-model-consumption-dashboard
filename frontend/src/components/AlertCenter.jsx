import React from 'react';
import { formatCurrency, formatTokens } from '../utils/formatters';

export default function AlertCenter({ alerts }) {
  return (
    <div className="card-panel" style={{ minHeight: '400px' }}>
      <h3 className="panel-title" style={{ borderBottom: '1px solid var(--border-color)', paddingBottom: '16px' }}>
        <span style={{color: 'var(--accent-rose)'}}>🚨</span> Budget Threshold Violation Center
      </h3>

      {alerts.length === 0 ? (
        <div style={{
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: '16px',
          color: 'var(--text-muted)'
        }}>
          <span style={{ fontSize: '48px' }}>🛡️</span>
          <div style={{ textAlign: 'center' }}>
            <h4 style={{ color: 'var(--ink)', marginBottom: '4px', fontSize: '15px' }}>All Systems Nominal</h4>
            <p style={{ fontSize: '13px' }}>No users or service accounts have violated budget alert thresholds.</p>
          </div>
        </div>
      ) : (
        <div className="alerts-list">
          {alerts.map((alert, idx) => {
            const isDanger = alert.severity === 'danger';
            const displayLimit = alert.metric === 'money' ? formatCurrency(alert.limit) : formatTokens(alert.limit);
            const displayConsumed = alert.metric === 'money' ? formatCurrency(alert.consumed) : formatTokens(alert.consumed);

            const periodLabel = { day: 'daily', week: 'weekly', month: 'monthly', year: 'yearly' }[alert.period] ?? `${alert.period}ly`;
            return (
              <div key={idx} className={`alert-banner ${alert.severity}`}>
                <div className="alert-message-col">
                  <span className="alert-message">
                    {isDanger ? '🛑 DANGER: ' : '⚠️ WARNING: '}
                    <strong>{alert.identity}</strong> has consumed <strong>{alert.percentage}%</strong> of their {periodLabel} {alert.metric} budget!
                  </span>
                  <span className="alert-subtext">
                    Threshold: {alert.threshold_percentage}% | Limit: {displayLimit} | Consumed: {displayConsumed}
                    {alert.hard_limit_enabled && " | hard-limit flag set (enforcement not yet implemented)"}
                  </span>
                </div>
                <div>
                  <span style={{
                    fontSize: '11px',
                    fontWeight: '700',
                    padding: '4px 10px',
                    borderRadius: '4px',
                    backgroundColor: isDanger ? 'var(--status-crit-bg)' : 'var(--status-warn-bg)',
                    color: isDanger ? 'var(--status-crit)' : 'var(--status-warn-text)',
                    border: '1px solid currentColor'
                  }}>
                    {isDanger ? 'LIMIT REACHED' : 'WARNING SPIKE'}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
