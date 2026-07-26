import React from 'react';
import { formatCurrency } from '../utils/formatters';

export default function CostAnomalyBanner({ anomaly, onDismiss }) {
  if (!anomaly || !anomaly.is_anomaly) return null;

  const { today_cost_usd, baseline_daily_avg_usd, ratio, baseline_days, per_user } = anomaly;

  const ratioText = ratio != null
    ? `${ratio.toFixed(1)}× the ${formatCurrency(baseline_daily_avg_usd)}/day average over the last ${baseline_days} days`
    : `vs. ${formatCurrency(baseline_daily_avg_usd)}/day average over the last ${baseline_days} days`;

  const anomalousUsers = (per_user || []).filter(u => u.is_anomaly).slice(0, 3);
  const drivenByText = anomalousUsers.length > 0
    ? anomalousUsers.map(u => `${u.user_email} (${u.ratio != null ? `${u.ratio.toFixed(1)}×` : 'n/a'})`).join(', ')
    : null;

  return (
    <div style={{
      marginBottom: '20px',
      padding: '10px 14px',
      borderRadius: 'var(--radius)',
      background: 'var(--status-crit-bg)',
      border: '1px solid var(--status-crit)',
      fontSize: '12px',
      color: 'var(--status-crit)',
      fontWeight: '500',
      display: 'flex',
      alignItems: 'flex-start',
      justifyContent: 'space-between',
      gap: '12px',
    }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
        <span>
          ⚠ Cost spike: today's spend is {formatCurrency(today_cost_usd)} ({ratioText}).
        </span>
        {drivenByText && (
          <span style={{ fontSize: '11px' }}>
            Driven by: {drivenByText}.
          </span>
        )}
      </div>
      <button
        onClick={onDismiss}
        style={{
          background: 'none',
          border: 'none',
          color: 'var(--status-crit)',
          cursor: 'pointer',
          fontSize: '16px',
          lineHeight: '1',
          padding: '0 4px',
          flexShrink: 0,
        }}
        aria-label="Dismiss cost spike notice"
      >
        ✕
      </button>
    </div>
  );
}
