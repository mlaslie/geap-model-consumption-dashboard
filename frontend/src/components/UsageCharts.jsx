import React from 'react';
import { formatCurrency, formatTokens } from '../utils/formatters';

const RANGES = [
  { value: 'today',   label: 'Today' },
  { value: 'week',    label: 'Week'  },
  { value: 'month',   label: 'Month' },
  { value: '6months', label: '6 Mo'  },
  { value: 'year',    label: 'Year'  },
];

const RANGE_TITLES = {
  today:    'Cost Trend — Today',
  week:     'Cost Trend — Last 7 Days',
  month:    'Cost Trend — Last 30 Days',
  '6months':'Cost Trend — Last 6 Months',
  year:     'Cost Trend — Last 12 Months',
};

// Model Share follows the chart's data window for ranges beyond the default
// 30-day dashboard feed, so its subtitle must say which window it reflects.
const SHARE_WINDOW_LABELS = {
  today:    'last 30 days',   // <=30d ranges reuse the 30-day dashboard feed
  week:     'last 30 days',
  month:    'last 30 days',
  '6months':'last 6 months',
  year:     'last 12 months',
};

const SERIES_COLORS = [
  'var(--series-1)',
  'var(--series-2)',
  'var(--series-3)',
  'var(--series-4)',
  'var(--series-5)',
];
const OTHER_COLOR = 'var(--ink-3)';

function formatHourLabel(h) {
  if (h === 0)  return '12a';
  if (h < 12)   return `${h}a`;
  if (h === 12) return '12p';
  return `${h - 12}p`;
}

function getWeekStartKey(dateStr) {
  const [y, m, d] = dateStr.split('-').map(Number);
  const dateMs = Date.UTC(y, m - 1, d);
  const dayOfWeek = new Date(dateMs).getUTCDay(); // 0=Sun
  return new Date(dateMs - dayOfWeek * 86400000).toISOString().split('T')[0];
}

function getBucketKey(ts, range) {
  if (!ts) return null;
  const dateStr = ts.split('T')[0];
  if (range === 'today') {
    const hourStr = ts.length > 10 ? ts.substring(11, 13) : '00';
    return hourStr.padStart(2, '0');
  }
  if (range === 'week' || range === 'month') {
    return dateStr;
  }
  if (range === '6months') {
    return getWeekStartKey(dateStr);
  }
  // year
  return dateStr.substring(0, 7);
}

function getSeedKeys(range, nowUTC) {
  const todayUTCMs = Date.UTC(nowUTC.getUTCFullYear(), nowUTC.getUTCMonth(), nowUTC.getUTCDate());
  if (range === 'today') {
    return Array.from({ length: 24 }, (_, i) => String(i).padStart(2, '0'));
  }
  if (range === 'week') {
    return Array.from({ length: 7 }, (_, i) =>
      new Date(todayUTCMs - (6 - i) * 86400000).toISOString().split('T')[0]
    );
  }
  if (range === 'month') {
    return Array.from({ length: 30 }, (_, i) =>
      new Date(todayUTCMs - (29 - i) * 86400000).toISOString().split('T')[0]
    );
  }
  if (range === '6months') {
    // 27 Sunday-aligned weeks: 26 spans only 182 days from the latest Sunday,
    // but the fetch window is 183 days back from TODAY — rows from the extra
    // partial week would silently drop out of the seeded buckets.
    const dayOfWeek = nowUTC.getUTCDay(); // 0=Sun
    const latestSunday = todayUTCMs - dayOfWeek * 86400000;
    return Array.from({ length: 27 }, (_, i) =>
      new Date(latestSunday - (26 - i) * 7 * 86400000).toISOString().split('T')[0]
    );
  }
  // year: 13 calendar months — the 366-day fetch window reaches into the same
  // calendar month one year ago, which is a 13th distinct month bucket; with
  // only 12 seeded, that partial month's rows would silently drop.
  return Array.from({ length: 13 }, (_, i) => {
    const d = new Date(Date.UTC(nowUTC.getUTCFullYear(), nowUTC.getUTCMonth() - (12 - i), 1));
    return d.toISOString().split('T')[0].substring(0, 7);
  });
}

function getBucketLabel(key, range) {
  if (range === 'today') {
    return formatHourLabel(parseInt(key, 10));
  }
  if (range === 'week') {
    const [y, m, d] = key.split('-').map(Number);
    return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
  }
  if (range === 'month' || range === '6months') {
    // Dense ranges: compact numeric M/D labels so they fit narrow columns
    // (long "Jun 22" labels ellipsize at 30 buckets).
    const [, m, d] = key.split('-').map(Number);
    return `${m}/${d}`;
  }
  // year: "YYYY-MM"
  const [y, mo] = key.split('-').map(Number);
  return new Date(Date.UTC(y, mo - 1, 1)).toLocaleDateString('en-US', { month: 'short', timeZone: 'UTC' });
}

function shouldShowLabel(idx, range) {
  if (range === 'today')    return idx % 4 === 0;
  if (range === 'month')    return idx % 5 === 0;
  if (range === '6months')  return idx % 4 === 0;
  return true; // week, year
}

// Model Share panel: stable heuristic coloring by name pattern
function getModelColorShare(modelName) {
  if (modelName.includes('2.5-pro') || modelName.includes('3.1-pro')) return 'var(--series-1)';
  if (modelName.includes('3.5-flash') || modelName.includes('3.6-flash')) return 'var(--series-2)';
  if (modelName.includes('2.5-flash')) return 'var(--series-3)';
  if (modelName.includes('3.1-flash-lite')) return 'var(--series-4)';
  if (modelName.includes('3-flash-preview')) return 'var(--series-5)';
  return 'var(--series-4)';
}

export default function UsageCharts({ logs, loggingConfig, chartRange = 'month', setChartRange, chartLoading = false, chartTruncated = false }) {
  const nowUTC = new Date();
  const todayUTCMs = Date.UTC(nowUTC.getUTCFullYear(), nowUTC.getUTCMonth(), nowUTC.getUTCDate());

  // Cutoff: only rows on or after this UTC midnight count for the chart window
  const cutoffMsMap = {
    today:    todayUTCMs,
    week:     todayUTCMs - 6 * 86400000,
    month:    todayUTCMs - 29 * 86400000,
    '6months':todayUTCMs - 182 * 86400000,
    year:     todayUTCMs - 365 * 86400000,
  };
  const cutoffMs = cutoffMsMap[chartRange] ?? cutoffMsMap.month;

  // Filter rows to the current time window
  const windowRows = logs.filter(log => {
    if (!log.call_timestamp) return false;
    const dateStr = log.call_timestamp.split('T')[0];
    const [y, m, d] = dateStr.split('-').map(Number);
    return Date.UTC(y, m - 1, d) >= cutoffMs;
  });

  // Build model-total-cost map across windowRows for stable color assignment
  const modelTotals = {};
  windowRows.forEach(log => {
    if (log.model_name) {
      modelTotals[log.model_name] = (modelTotals[log.model_name] || 0) + (log.estimated_cost_usd || 0);
    }
  });
  // Sort models by total cost descending
  const sortedModels = Object.entries(modelTotals)
    .sort((a, b) => b[1] - a[1])
    .map(([model]) => model);

  // Top-5 get series-1..5; the rest fold into 'Other'
  const primaryModels = sortedModels.slice(0, 5);
  const hasOtherModels = sortedModels.length > 5;

  const modelColor = (model) => {
    const idx = primaryModels.indexOf(model);
    return idx >= 0 ? SERIES_COLORS[idx] : OTHER_COLOR;
  };

  // Seed all bucket keys so the chart always has the full set of columns
  const seedKeys = getSeedKeys(chartRange, nowUTC);

  // Build per-bucket cost data: { bucketKey: { modelOrOther: cost } }
  const bucketData = {};
  seedKeys.forEach(k => { bucketData[k] = {}; });

  windowRows.forEach(log => {
    const key = getBucketKey(log.call_timestamp, chartRange);
    if (!key || !(key in bucketData)) return;
    const modelKey = log.model_name
      ? (primaryModels.includes(log.model_name) ? log.model_name : 'Other')
      : null;
    if (!modelKey) return;
    bucketData[key][modelKey] = (bucketData[key][modelKey] || 0) + (log.estimated_cost_usd || 0);
  });

  // Build chart entries
  const chartEntries = seedKeys.map(key => {
    const segments = bucketData[key];
    const total = Object.values(segments).reduce((s, v) => s + v, 0);
    return { key, label: getBucketLabel(key, chartRange), segments, total };
  });

  const maxTotal = Math.max(...chartEntries.map(e => e.total), 1.0);

  // Legend entries: only models that have data in this window
  const legendEntries = primaryModels
    .filter(m => (modelTotals[m] || 0) > 0)
    .map((m, i) => ({ label: m, color: SERIES_COLORS[i] }));
  if (hasOtherModels) {
    const otherHasData = sortedModels.slice(5).some(m => (modelTotals[m] || 0) > 0);
    if (otherHasData) legendEntries.push({ label: 'Other', color: OTHER_COLOR });
  }

  // Segment render order: with flex-direction: column-reverse,
  // the FIRST item in DOM ends up at the BOTTOM visually.
  // primaryModels[0] is the most expensive → should be at bottom → render first.
  const segmentOrder = [...primaryModels, ...(hasOtherModels ? ['Other'] : [])];

  const titleText = RANGE_TITLES[chartRange] || 'Cost Trend';

  // ── Model Share panel (unchanged logic) ──────────────────────────────────
  const modelShare = {};
  if (loggingConfig) {
    Object.entries(loggingConfig).forEach(([model, enabled]) => {
      if (enabled) modelShare[model] = 0;
    });
  }
  let totalShareTokens = 0;
  logs.forEach(log => {
    const model = log.model_name;
    if (model) {
      if (!(model in modelShare)) modelShare[model] = 0;
      modelShare[model] += log.total_tokens || 0;
      totalShareTokens += log.total_tokens || 0;
    }
  });
  Object.keys(modelShare).forEach(model => {
    const isActivated = loggingConfig && loggingConfig[model] === true;
    const hasTokens = modelShare[model] > 0;
    if (!isActivated && !hasTokens) delete modelShare[model];
  });

  return (
    <div className="dashboard-grid">
      {/* Cost Trend Chart */}
      <div className="card-panel">
        {/* Header row: title + range selector */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '18px', gap: '12px', flexWrap: 'wrap' }}>
          <h3 className="panel-title" style={{ margin: 0 }}>
            <span style={{ color: 'var(--accent-indigo)' }}>📊</span> {titleText}
          </h3>
          <div className="chart-range-toggle" role="group" aria-label="Chart time range">
            {RANGES.map(({ value, label }) => (
              <button
                key={value}
                className={`chart-range-btn${chartRange === value ? ' active' : ''}`}
                onClick={() => setChartRange && setChartRange(value)}
                aria-pressed={chartRange === value}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        {/* Loading indicator */}
        {chartLoading && (
          <div style={{ fontSize: '11px', color: 'var(--ink-3)', marginBottom: '8px', fontStyle: 'italic' }}>
            Loading range…
          </div>
        )}

        {/* Row-cap warning for extended ranges */}
        {chartTruncated && !chartLoading && (
          <div style={{ fontSize: '11px', color: 'var(--status-warn-text, var(--status-warn))', marginBottom: '8px' }}>
            ⚠ Range hit the row cap — older buckets may undercount.
          </div>
        )}

        {/* Bars */}
        <div className="chart-container">
          {chartEntries.map(({ key, label, segments, total }, idx) => {
            const heightPercent = (total / maxTotal) * 80;
            const showLabel = shouldShowLabel(idx, chartRange);

            // Tooltip lines: per-model breakdown (only models present in this bucket)
            const tooltipBreakdown = segmentOrder.filter(m => (segments[m] || 0) > 0);

            return (
              <div key={key} className="chart-bar-col">
                <div className="chart-bar-wrapper">
                  {total > 0 && (
                    <div
                      className="chart-stack-bar"
                      style={{ height: `${Math.max(heightPercent, 4)}%` }}
                    >
                      {segmentOrder.map(m => {
                        const cost = segments[m] || 0;
                        if (cost <= 0) return null;
                        const color = m === 'Other' ? OTHER_COLOR : modelColor(m);
                        return (
                          <div
                            key={m}
                            className="chart-stack-segment"
                            style={{ flex: cost, backgroundColor: color }}
                          />
                        );
                      })}
                    </div>
                  )}
                </div>
                <div className="chart-bar-tooltip multiline">
                  <div style={{ fontWeight: '700', marginBottom: '3px' }}>{label}</div>
                  <div style={{ marginBottom: '4px', borderBottom: '1px solid rgba(255,255,255,0.12)', paddingBottom: '4px' }}>
                    Total: {formatCurrency(total)}
                  </div>
                  {tooltipBreakdown.map(m => (
                    <div key={m} style={{ color: 'rgba(232,237,243,0.75)', fontSize: '10px', display: 'flex', alignItems: 'center', gap: '4px' }}>
                      <span style={{ display: 'inline-block', width: '6px', height: '6px', borderRadius: '1px', backgroundColor: m === 'Other' ? OTHER_COLOR : modelColor(m), flexShrink: 0 }} />
                      {m}: {formatCurrency(segments[m] || 0)}
                    </div>
                  ))}
                </div>
                <div
                  className="chart-axis-label"
                  style={{ visibility: showLabel ? 'visible' : 'hidden' }}
                >
                  {label}
                </div>
              </div>
            );
          })}
        </div>

        {/* Legend */}
        {legendEntries.length > 0 && (
          <div className="chart-legend">
            {legendEntries.map(({ label, color }) => (
              <div key={label} className="chart-legend-item">
                <span className="legend-dot" style={{ backgroundColor: color }} />
                <span>{label}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Model Share panel */}
      <div className="card-panel">
        <h3 className="panel-title">
          <span style={{ color: 'var(--accent-emerald)' }}>🍰</span> Model Share (by Tokens)
        </h3>
        <div style={{ fontSize: '11px', color: 'var(--ink-3)', marginTop: '-6px', marginBottom: '10px' }}>
          {SHARE_WINDOW_LABELS[chartRange] || 'last 30 days'}
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center', height: '100%', gap: '20px' }}>
          {Object.keys(modelShare).length === 0 ? (
            <div style={{ textAlign: 'center', color: 'var(--text-muted)', padding: '20px' }}>
              No active tokens or activated models to display.
            </div>
          ) : (
            <div className="model-pill-legend model-share-scroll">
              {/* Largest consumption first; the list scrolls independently so
                  a long model roster can't stretch the dashboard row (and the
                  Cost Trend chart with it). */}
              {Object.entries(modelShare).sort((a, b) => b[1] - a[1]).map(([model, tokens]) => {
                const pct = totalShareTokens > 0 ? (tokens / totalShareTokens) * 100 : 0;
                const dotColor = getModelColorShare(model);
                return (
                  <div key={model} className="legend-item">
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                      <span className="legend-dot" style={{ backgroundColor: dotColor }} />
                      <span style={{ fontSize: '13px', fontWeight: '500' }}>{model}</span>
                    </div>
                    <div style={{ textAlign: 'right' }}>
                      <div style={{ fontSize: '13px', fontFamily: 'var(--font-mono)', fontWeight: '600' }}>{pct.toFixed(1)}%</div>
                      <div style={{ fontSize: '10px', color: 'var(--text-muted)' }}>{formatTokens(tokens)} tokens</div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
