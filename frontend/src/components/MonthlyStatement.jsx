import React, { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../utils/api';
import { toCsv, downloadCsv, csvTimestamp } from '../utils/csv';
import { formatCurrency, formatTokens } from '../utils/formatters';

function currentUtcMonth() {
  return new Date().toISOString().slice(0, 7);
}

function friendlyMonth(yyyyMm) {
  if (!yyyyMm) return '';
  const [y, m] = yyyyMm.split('-');
  const d = new Date(Date.UTC(Number(y), Number(m) - 1, 1));
  return d.toLocaleDateString('en-US', { month: 'long', year: 'numeric', timeZone: 'UTC' });
}

const STMT_CSV_COLUMNS = [
  { key: 'month',          header: 'month' },
  { key: 'principal',      header: 'principal' },
  { key: 'calls',          header: 'calls' },
  { key: 'input_tokens',   header: 'input_tokens' },
  { key: 'output_tokens',  header: 'output_tokens' },
  { key: 'thoughts_tokens',header: 'thoughts_tokens' },
  { key: 'total_tokens',   header: 'total_tokens' },
  { key: 'cost_usd',       header: 'cost_usd' },
  { key: 'models',         header: 'models' },
];

export default function MonthlyStatement() {
  const [month, setMonth] = useState(currentUtcMonth());
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const fetchStatement = useCallback(async (m) => {
    setLoading(true);
    setError(null);
    setData(null);
    try {
      const res = await apiFetch(`/api/statements?month=${m}`);
      if (!res.ok) {
        const body = await res.json();
        setError(body.detail || `Error ${res.status}`);
        return;
      }
      const body = await res.json();
      setData(body.data);
    } catch (e) {
      setError(e.message || 'Network error');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchStatement(month);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleGenerate = () => fetchStatement(month);

  const handleDownload = () => {
    if (!data || !data.per_principal || data.per_principal.length === 0) return;
    const csvRows = data.per_principal.map(p => ({
      month:          data.month,
      principal:      p.user_email,
      calls:          p.calls,
      input_tokens:   p.input_tokens,
      output_tokens:  p.output_tokens,
      thoughts_tokens: p.thoughts_tokens,
      total_tokens:   p.total_tokens,
      cost_usd:       p.cost_usd,
      // Semicolon-joined so models list survives CSV parsing
      models:         (p.models || []).join(';'),
    }));
    downloadCsv(
      `statement-${data.month}-${csvTimestamp()}.csv`,
      toCsv(STMT_CSV_COLUMNS, csvRows),
    );
  };

  return (
    <div className="card-panel">
      {/* ── Selector header ──────────────────────────────────────────────── */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '20px', flexWrap: 'wrap', gap: '12px' }}>
        <div>
          <h2 style={{ fontSize: '18px', fontWeight: '600', color: 'var(--ink)', marginBottom: '4px' }}>
            Monthly Statement
          </h2>
          <p style={{ fontSize: '12px', color: 'var(--ink-3)' }}>
            Cost and token breakdown by principal and model for a calendar month.
          </p>
        </div>
        <div className="table-actions">
          <input
            type="month"
            value={month}
            onChange={e => setMonth(e.target.value)}
            aria-label="Statement month"
            style={{
              background: 'var(--surface-2)',
              border: '1px solid var(--line)',
              color: 'var(--ink)',
              borderRadius: 'var(--radius)',
              padding: '6px 12px',
              fontSize: '13px',
            }}
          />
          <button
            type="button"
            className="btn-primary"
            onClick={handleGenerate}
            disabled={loading || !month}
          >
            Generate
          </button>
        </div>
      </div>

      {/* ── Loading ──────────────────────────────────────────────────────── */}
      {loading && (
        <p style={{ color: 'var(--ink-3)', fontSize: '13px', padding: '20px 0' }}>
          Loading statement…
        </p>
      )}

      {/* ── Error ────────────────────────────────────────────────────────── */}
      {!loading && error && (
        <p style={{ color: 'var(--status-crit)', fontSize: '13px', padding: '12px 0' }}>
          {error}
        </p>
      )}

      {/* ── Statement content ────────────────────────────────────────────── */}
      {!loading && data && (
        <div>
          {/* Header block */}
          <div style={{ marginBottom: '20px', paddingBottom: '16px', borderBottom: '1px solid var(--line)' }}>
            <h3 style={{ fontSize: '16px', fontWeight: '600', color: 'var(--ink)', marginBottom: '6px' }}>
              Statement — {friendlyMonth(data.month)}
            </h3>
            <p style={{ fontSize: '12px', color: 'var(--ink-3)' }}>
              Period: {data.period_start} — {data.period_end_exclusive} (exclusive)
            </p>
            <p style={{ fontSize: '12px', color: 'var(--ink-3)' }}>
              Generated: {data.generated_at_utc}
            </p>
          </div>

          {/* Totals strip */}
          <div style={{ display: 'flex', gap: '24px', flexWrap: 'wrap', marginBottom: '24px', padding: '16px', background: 'var(--surface-2)', borderRadius: 'var(--radius)', border: '1px solid var(--line-soft)' }}>
            <div>
              <div style={{ fontSize: '11px', color: 'var(--ink-3)', marginBottom: '2px' }}>Total Cost</div>
              <div style={{ fontFamily: 'var(--font-mono)', fontWeight: '600', color: 'var(--ink)' }}>
                {formatCurrency(data.totals.cost_usd)}
              </div>
            </div>
            <div>
              <div style={{ fontSize: '11px', color: 'var(--ink-3)', marginBottom: '2px' }}>Calls</div>
              <div style={{ fontFamily: 'var(--font-mono)', fontWeight: '600', color: 'var(--ink)' }}>
                {data.totals.calls}
              </div>
            </div>
            <div>
              <div style={{ fontSize: '11px', color: 'var(--ink-3)', marginBottom: '2px' }}>Principals</div>
              <div style={{ fontFamily: 'var(--font-mono)', fontWeight: '600', color: 'var(--ink)' }}>
                {data.totals.principals}
              </div>
            </div>
            <div>
              <div style={{ fontSize: '11px', color: 'var(--ink-3)', marginBottom: '2px' }}>Total Tokens</div>
              <div style={{ fontFamily: 'var(--font-mono)', fontWeight: '600', color: 'var(--ink)' }}>
                {formatTokens(data.totals.total_tokens)}
              </div>
              {data.totals.thoughts_tokens > 0 && (
                <div style={{ fontSize: '11px', color: 'var(--ink-3)' }}>
                  incl. {formatTokens(data.totals.thoughts_tokens)} reasoning
                </div>
              )}
            </div>
          </div>

          {/* Per-principal table */}
          <div style={{ marginBottom: '24px' }}>
            <div className="table-header-row" style={{ marginBottom: '12px' }}>
              <h4 style={{ fontSize: '14px', fontWeight: '600', color: 'var(--ink)' }}>By Principal</h4>
              <div className="table-actions">
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={handleDownload}
                  disabled={!data.per_principal || data.per_principal.length === 0}
                  title={
                    !data.per_principal || data.per_principal.length === 0
                      ? 'No data to export'
                      : `Export ${data.per_principal.length} principal(s) as CSV`
                  }
                >
                  ⤓ Download statement CSV
                </button>
              </div>
            </div>
            <div className="custom-table-container">
              <table className="custom-table">
                <thead>
                  <tr>
                    <th>Principal</th>
                    <th style={{ textAlign: 'center' }}>Calls</th>
                    <th>Tokens</th>
                    <th>Cost (USD)</th>
                    <th>Models Used</th>
                  </tr>
                </thead>
                <tbody>
                  {!data.per_principal || data.per_principal.length === 0 ? (
                    <tr>
                      <td colSpan={5} style={{ textAlign: 'center', color: 'var(--ink-3)', padding: '20px' }}>
                        No usage in this period.
                      </td>
                    </tr>
                  ) : (
                    data.per_principal.map(p => (
                      <tr key={p.user_email}>
                        <td className="identity-cell">
                          <span className="identity-email">{p.user_email}</span>
                        </td>
                        <td style={{ fontFamily: 'var(--font-mono)', fontSize: '12px', textAlign: 'center' }}>
                          {p.calls}
                        </td>
                        <td style={{ fontFamily: 'var(--font-mono)', fontSize: '12px' }}>
                          {formatTokens(p.total_tokens)}
                        </td>
                        <td style={{ fontFamily: 'var(--font-mono)', fontSize: '12px' }}>
                          {formatCurrency(p.cost_usd)}
                        </td>
                        <td style={{ fontSize: '12px', color: 'var(--ink-2)' }}>
                          {(p.models || []).join(', ')}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {/* Per-model table */}
          <div style={{ marginBottom: '24px' }}>
            <h4 style={{ fontSize: '14px', fontWeight: '600', color: 'var(--ink)', marginBottom: '12px' }}>By Model</h4>
            <div className="custom-table-container">
              <table className="custom-table">
                <thead>
                  <tr>
                    <th>Model</th>
                    <th style={{ textAlign: 'center' }}>Calls</th>
                    <th>Tokens</th>
                    <th>Cost (USD)</th>
                  </tr>
                </thead>
                <tbody>
                  {!data.per_model || data.per_model.length === 0 ? (
                    <tr>
                      <td colSpan={4} style={{ textAlign: 'center', color: 'var(--ink-3)', padding: '20px' }}>
                        No models in this period.
                      </td>
                    </tr>
                  ) : (
                    data.per_model.map(m => (
                      <tr key={m.model_name}>
                        <td style={{ fontFamily: 'var(--font-mono)', fontSize: '12px' }}>{m.model_name}</td>
                        <td style={{ fontFamily: 'var(--font-mono)', fontSize: '12px', textAlign: 'center' }}>{m.calls}</td>
                        <td style={{ fontFamily: 'var(--font-mono)', fontSize: '12px' }}>{formatTokens(m.total_tokens)}</td>
                        <td style={{ fontFamily: 'var(--font-mono)', fontSize: '12px' }}>{formatCurrency(m.cost_usd)}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {/* Unpriced models warning */}
          {data.unpriced_models && data.unpriced_models.length > 0 && (
            <p style={{ color: 'var(--status-warn-text)', fontSize: '12px', marginBottom: '12px' }}>
              {data.unpriced_models.length} model(s) in this period have no configured pricing; their cost is counted as $0.
            </p>
          )}

          {/* Footnote: pricing assumptions */}
          {data.pricing_assumptions && (
            <p className="bc-footnote">
              Pricing rates as of {data.pricing_assumptions.rates_as_of_utc}.
              {data.pricing_assumptions.note ? ` ${data.pricing_assumptions.note}` : ''}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
