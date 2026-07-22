import React from 'react';
import { formatCurrency, formatTokens } from '../utils/formatters';

export default function SummaryCards({ logs, alerts }) {
  // Compute metrics
  const totalCost = logs.reduce((sum, log) => sum + (log.estimated_cost_usd || 0), 0);
  const hasUnpriced = logs.some(log => log.pricing_match === 'default');
  const totalTokens = logs.reduce((sum, log) => sum + (log.total_tokens || 0), 0);
  
  // Group by user to find the top spender
  const userSpend = {};
  logs.forEach(log => {
    const user = log.user_email;
    userSpend[user] = (userSpend[user] || 0) + (log.estimated_cost_usd || 0);
  });
  
  let topUser = 'N/A';
  let topUserSpend = 0;
  Object.entries(userSpend).forEach(([user, spend]) => {
    if (spend > topUserSpend) {
      topUserSpend = spend;
      topUser = user;
    }
  });

  // Shorten top user email for layout spacing
  const displayTopUser = topUser.length > 20 ? topUser.split('@')[0] + '@...' : topUser;

  return (
    <div className="kpi-grid">
      <div className="kpi-card indigo-hover">
        <div className="kpi-header">
          <span>Enterprise Cost</span>
          <span style={{color: 'var(--accent-indigo)'}}>USD</span>
        </div>
        <div className="kpi-value">{formatCurrency(totalCost)}</div>
        <div className="kpi-footer">
          Accumulated cloud spend (30d)
          {hasUnpriced && (
            <span style={{ fontSize: '10px', color: 'var(--ink-3)' }}> · excludes unpriced models</span>
          )}
        </div>
      </div>
      
      <div className="kpi-card emerald-hover">
        <div className="kpi-header">
          <span>Total Tokens</span>
          <span style={{color: 'var(--accent-emerald)'}}>INT64</span>
        </div>
        <div className="kpi-value">{formatTokens(totalTokens)}</div>
        <div className="kpi-footer">Input + Output Vertex SDK tokens</div>
      </div>

      <div className="kpi-card rose-hover">
        <div className="kpi-header">
          <span>Active Alerts</span>
          <span style={{color: 'var(--accent-rose)'}}>ALRT</span>
        </div>
        <div className="kpi-value" style={{color: alerts.length > 0 ? 'var(--status-crit)' : 'var(--ink)'}}>
          {alerts.length}
        </div>
        <div className="kpi-footer">Users breaching thresholds</div>
      </div>

      <div className="kpi-card amber-hover">
        <div className="kpi-header">
          <span>Top Consumer</span>
          <span style={{color: 'var(--accent-amber)'}}>USER</span>
        </div>
        <div className="kpi-value" style={{fontSize: '18px', padding: '4px 0'}} title={topUser}>
          {displayTopUser}
        </div>
        <div className="kpi-footer">Spent {formatCurrency(topUserSpend)}</div>
      </div>
    </div>
  );
}
