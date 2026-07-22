import React, { useState, useEffect } from 'react';
import { apiFetch } from '../utils/api';
import { formatCurrency, formatDateTime } from '../utils/formatters';

const MODEL_ID_REGEX = /^[A-Za-z0-9.-]{1,100}$/;
const TERM_DAYS = { day: 1, week: 7, month: 30, year: 365 };

/* ── Shared notification banner (same visual spec as ModelLogging) ── */
function Notification({ notif }) {
  if (!notif) return null;
  const isSuccess = notif.type === 'success';
  return (
    <div style={{
      backgroundColor: isSuccess ? 'var(--accent-emerald-glow)' : 'var(--status-crit-bg)',
      border: `1px solid ${isSuccess ? 'var(--series-3)' : 'var(--status-crit)'}`,
      borderRadius: 'var(--radius)',
      padding: '12px 16px',
      color: isSuccess ? 'var(--status-good)' : 'var(--status-crit)',
      fontSize: '13px',
      fontWeight: '500',
      marginBottom: '20px',
      display: 'flex',
      alignItems: 'center',
      gap: '8px',
    }}>
      <span>{isSuccess ? '✅' : '⚠️'}</span>
      <span>{notif.message}</span>
    </div>
  );
}

/* ── Line cost helper — region and longContext are UI-only, not persisted ── */
function getLineCost(item, pricing) {
  const rates = pricing[item.model];
  if (!rates) return { cost: 0, missingModel: true };

  const gt200kVal = rates.input_cost_per_million_gt_200k;
  const hasGt200k = gt200kVal !== undefined && gt200kVal !== null && gt200kVal !== '';
  const useGt200k = item.longContext && hasGt200k;
  const inputRate = useGt200k
    ? (parseFloat(gt200kVal) || 0)
    : (parseFloat(rates.input_cost_per_million) || 0);
  const outputRate = parseFloat(rates.output_cost_per_million) || 0;

  const isRegional = item.region === 'regional';
  const multiplier = isRegional
    ? (parseFloat(rates.non_global_multiplier) || 1.0)
    : 1.0;

  const inputTok = parseInt(item.input_tokens) || 0;
  const outputTok = parseInt(item.output_tokens) || 0;
  const cost = ((inputTok / 1e6) * inputRate + (outputTok / 1e6) * outputRate) * multiplier;
  return { cost, missingModel: false };
}

/* ── Normalize all lines to daily → monthly → yearly ── */
function computeBuilderTotals(items, pricing) {
  let totalDaily = 0;
  const perLine = items.map(item => {
    const { cost, missingModel } = getLineCost(item, pricing);
    const termDays = TERM_DAYS[item.term] || 1;
    const daily = cost / termDays;
    totalDaily += daily;
    return { cost, daily, monthly: daily * 30, missingModel };
  });
  return { daily: totalDaily, monthly: totalDaily * 30, yearly: totalDaily * 365, perLine };
}

function getSavedEstimateTotals(est, pricing) {
  let totalDaily = 0;
  (est.items || []).forEach(item => {
    /* Saved items have no region/longContext — default to global/off */
    const { cost } = getLineCost(
      { ...item, region: 'global', longContext: false },
      pricing,
    );
    const termDays = TERM_DAYS[item.term] || 1;
    totalDaily += cost / termDays;
  });
  return { monthly: totalDaily * 30, yearly: totalDaily * 365 };
}

/* ── Empty builder line factory ── */
function makeEmptyLine(modelIds) {
  return {
    model: modelIds[0] || '',
    input_tokens: 0,
    output_tokens: 0,
    term: 'month',
    note: '',
    region: 'global',
    longContext: false,
  };
}

export default function PricingPlanner({ unpricedModels = [] }) {
  /* ── Pricing state ── */
  const [editedPricing, setEditedPricing] = useState({});
  const [pricingLoading, setPricingLoading] = useState(true);
  const [pricingSaving, setPricingSaving] = useState(false);
  const [pricingNotif, setPricingNotif] = useState(null);

  /* Add-model form */
  const [newModelId, setNewModelId] = useState('');
  const [newInputRate, setNewInputRate] = useState('');
  const [newGt200kRate, setNewGt200kRate] = useState('');
  const [newOutputRate, setNewOutputRate] = useState('');
  const [newMultiplier, setNewMultiplier] = useState('');
  const [newModelError, setNewModelError] = useState('');

  /* ── Estimates state ── */
  const [estimates, setEstimates] = useState({});
  const [estimatesLoading, setEstimatesLoading] = useState(true);
  const [estimateSaving, setEstimateSaving] = useState(false);
  const [estimatesNotif, setEstimatesNotif] = useState(null);

  /* Builder */
  const [builderName, setBuilderName] = useState('');
  const [builderItems, setBuilderItems] = useState([]);
  const [builderErrors, setBuilderErrors] = useState({});

  /* Unpriced model detection — raw model names from usage API with pricing_match === 'default' */
  const [rawUsageUnpricedModels, setRawUsageUnpricedModels] = useState([]);

  /* ── Load on mount ── */
  useEffect(() => { fetchPricing(); fetchEstimates(); fetchUsageForUnpriced(); }, []);

  /* ── Notification helpers ── */
  const showPricingNotif = (message, type = 'success') => {
    setPricingNotif({ message, type });
    setTimeout(() => setPricingNotif(null), 5000);
  };
  const showEstimatesNotif = (message, type = 'success') => {
    setEstimatesNotif({ message, type });
    setTimeout(() => setEstimatesNotif(null), 5000);
  };

  /* ── API calls ── */
  const fetchUsageForUnpriced = async () => {
    try {
      const res = await apiFetch('/api/usage?days=30');
      const json = await res.json();
      if (res.ok && json.status === 'success') {
        const models = Array.from(new Set(
          (json.data || [])
            .filter(row => row.pricing_match === 'default')
            .map(row => row.model_name)
            .filter(Boolean)
        ));
        setRawUsageUnpricedModels(models);
      }
    } catch {
      /* Silently ignore — unpriced detection is best-effort */
    }
  };

  const fetchPricing = async () => {
    setPricingLoading(true);
    try {
      const res = await apiFetch('/api/pricing');
      const json = await res.json();
      if (!res.ok) {
        showPricingNotif(`Failed to load pricing: ${json.detail || res.statusText}`, 'error');
      } else if (json.status === 'success') {
        setEditedPricing(JSON.parse(JSON.stringify(json.data)));
      }
    } catch (e) {
      if (e.message !== 'Unauthorized') showPricingNotif('Failed to load pricing data', 'error');
    } finally {
      setPricingLoading(false);
    }
  };

  const fetchEstimates = async () => {
    setEstimatesLoading(true);
    try {
      const res = await apiFetch('/api/estimates');
      const json = await res.json();
      if (!res.ok) {
        showEstimatesNotif(`Failed to load estimates: ${json.detail || res.statusText}`, 'error');
      } else if (json.status === 'success') {
        setEstimates(json.data);
      }
    } catch (e) {
      if (e.message !== 'Unauthorized') showEstimatesNotif('Failed to load estimates', 'error');
    } finally {
      setEstimatesLoading(false);
    }
  };

  /* ── Pricing handlers ── */
  const handleRateChange = (modelId, field, value) => {
    setEditedPricing(prev => ({ ...prev, [modelId]: { ...prev[modelId], [field]: value } }));
  };

  const handleDeleteModel = (modelId) => {
    if (!window.confirm(`Delete pricing for "${modelId}"? This will affect dashboard cost calculations when saved.`)) return;
    setEditedPricing(prev => { const next = { ...prev }; delete next[modelId]; return next; });
  };

  const handleAddModel = () => {
    setNewModelError('');
    const trimmedId = newModelId.trim();
    if (!MODEL_ID_REGEX.test(trimmedId)) {
      setNewModelError('Model ID must be 1–100 characters: letters, digits, dots, or hyphens only.');
      return;
    }
    if (editedPricing[trimmedId]) {
      setNewModelError(`"${trimmedId}" is already in the pricing table.`);
      return;
    }
    const newModel = {
      input_cost_per_million: parseFloat(newInputRate) || 0,
      output_cost_per_million: parseFloat(newOutputRate) || 0,
    };
    if (newGt200kRate.trim() !== '') {
      newModel.input_cost_per_million_gt_200k = parseFloat(newGt200kRate);
    }
    if (newMultiplier.trim() !== '') {
      newModel.non_global_multiplier = parseFloat(newMultiplier);
    }
    setEditedPricing(prev => ({ ...prev, [trimmedId]: newModel }));
    setNewModelId('');
    setNewInputRate('');
    setNewGt200kRate('');
    setNewOutputRate('');
    setNewMultiplier('');
  };

  const handleQuickAddModel = (modelId) => {
    if (!MODEL_ID_REGEX.test(modelId) || editedPricing[modelId]) return;
    setEditedPricing(prev => ({
      ...prev,
      [modelId]: { input_cost_per_million: 0, output_cost_per_million: 0 },
    }));
  };

  const handleSavePricing = async () => {
    setPricingSaving(true);
    try {
      const body = {};
      for (const [modelId, rates] of Object.entries(editedPricing)) {
        const modelData = {
          input_cost_per_million: parseFloat(rates.input_cost_per_million) || 0,
          output_cost_per_million: parseFloat(rates.output_cost_per_million) || 0,
        };
        /* Only send optional fields when they carry a real value */
        const gt200k = rates.input_cost_per_million_gt_200k;
        if (gt200k !== undefined && gt200k !== null && gt200k !== '') {
          modelData.input_cost_per_million_gt_200k = parseFloat(gt200k);
        }
        const mult = rates.non_global_multiplier;
        if (mult !== undefined && mult !== null && mult !== '') {
          modelData.non_global_multiplier = parseFloat(mult);
        }
        body[modelId] = modelData;
      }
      const res = await apiFetch('/api/pricing', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const json = await res.json();
      if (!res.ok) {
        showPricingNotif(`Failed to save pricing: ${json.detail || res.statusText}`, 'error');
      } else if (json.status === 'success') {
        setEditedPricing(JSON.parse(JSON.stringify(json.data)));
        showPricingNotif('Pricing saved — dashboard cost calculations updated immediately.');
      }
    } catch (e) {
      if (e.message !== 'Unauthorized') showPricingNotif('Error saving pricing', 'error');
    } finally {
      setPricingSaving(false);
    }
  };

  /* ── Builder handlers ── */
  const modelIds = Object.keys(editedPricing).sort();

  const addBuilderLine = () => {
    setBuilderItems(prev => [...prev, makeEmptyLine(modelIds)]);
  };

  const updateBuilderItem = (idx, field, value) => {
    setBuilderItems(prev => {
      const next = [...prev];
      next[idx] = { ...next[idx], [field]: value };
      return next;
    });
  };

  const removeBuilderItem = (idx) => {
    setBuilderItems(prev => prev.filter((_, i) => i !== idx));
  };

  const validateBuilder = () => {
    const errors = {};
    if (!builderName.trim()) errors.name = 'Estimate name is required.';
    else if (builderName.trim().length > 100) errors.name = 'Name must be ≤ 100 characters.';
    if (builderItems.length === 0) errors.items = 'Add at least one model line.';
    if (builderItems.length > 50) errors.items = 'Maximum 50 line items.';
    builderItems.forEach((item, i) => {
      if (parseInt(item.input_tokens) < 0) errors[`item_${i}_input`] = 'Must be ≥ 0';
      if (parseInt(item.output_tokens) < 0) errors[`item_${i}_output`] = 'Must be ≥ 0';
      if (item.note && item.note.length > 500) errors[`item_${i}_note`] = 'Note ≤ 500 chars';
    });
    return errors;
  };

  const handleSaveEstimate = async () => {
    const errors = validateBuilder();
    setBuilderErrors(errors);
    if (Object.keys(errors).length > 0) return;

    setEstimateSaving(true);
    try {
      const body = {
        name: builderName.trim(),
        /* region and longContext are UI-only and NOT persisted with the item */
        items: builderItems.map(item => ({
          model: item.model,
          input_tokens: parseInt(item.input_tokens) || 0,
          output_tokens: parseInt(item.output_tokens) || 0,
          term: item.term,
          note: item.note || '',
        })),
      };
      const res = await apiFetch('/api/estimates', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const json = await res.json();
      if (!res.ok) {
        showEstimatesNotif(`Failed to save estimate: ${json.detail || res.statusText}`, 'error');
      } else if (json.status === 'success') {
        setEstimates(prev => ({ ...prev, [json.data.name]: json.data }));
        showEstimatesNotif(`Estimate "${json.data.name}" saved.`);
      }
    } catch (e) {
      if (e.message !== 'Unauthorized') showEstimatesNotif('Error saving estimate', 'error');
    } finally {
      setEstimateSaving(false);
    }
  };

  const handleLoadEstimate = (est) => {
    setBuilderName(est.name);
    setBuilderItems((est.items || []).map(item => ({
      model: item.model,
      input_tokens: item.input_tokens,
      output_tokens: item.output_tokens,
      term: item.term,
      note: item.note || '',
      /* UI-only settings reset to defaults when loading a saved estimate */
      region: 'global',
      longContext: false,
    })));
    setBuilderErrors({});
  };

  const handleDeleteEstimate = async (name) => {
    if (!window.confirm(`Delete estimate "${name}"?`)) return;
    try {
      const res = await apiFetch(`/api/estimates/${encodeURIComponent(name)}`, { method: 'DELETE' });
      const json = await res.json();
      if (!res.ok) {
        showEstimatesNotif(`Failed to delete: ${json.detail || res.statusText}`, 'error');
      } else if (json.status === 'success') {
        setEstimates(prev => { const next = { ...prev }; delete next[name]; return next; });
        if (builderName === name) { setBuilderName(''); setBuilderItems([]); }
        showEstimatesNotif(`Estimate "${name}" deleted.`);
      }
    } catch (e) {
      if (e.message !== 'Unauthorized') showEstimatesNotif('Error deleting estimate', 'error');
    }
  };

  /* ── Detected unpriced models (union of prop + fetched, minus already-priced) ── */
  const pricingKeys = Object.keys(editedPricing);
  const detectedUnpriced = !pricingLoading
    ? Array.from(new Set([...rawUsageUnpricedModels, ...unpricedModels])).filter(model => {
        if (!model) return false;
        if (editedPricing[model]) return false;
        if (pricingKeys.some(k => model.startsWith(k))) return false;
        return true;
      })
    : [];

  /* ── Live computations ── */
  const builderTotals = computeBuilderTotals(builderItems, editedPricing);

  /* ── Shared compact number-input style used in pricing table ── */
  const rateInputStyle = { width: '120px', padding: '6px 10px' };

  /* ── Render ── */
  return (
    <div>
      {/* ═══ Section 1: Model Pricing ═══════════════════════════════════════════ */}
      <div className="card-panel" style={{ marginBottom: '24px', minHeight: 'auto' }}>
        <h3 className="panel-title" style={{ borderBottom: '1px solid var(--line)', paddingBottom: '16px', marginBottom: '20px' }}>
          💲 Model Pricing
        </h3>

        <Notification notif={pricingNotif} />

        {pricingLoading ? (
          <div style={{ padding: '24px 0', textAlign: 'center', color: 'var(--ink-2)', fontSize: '13px' }}>
            Loading pricing data...
          </div>
        ) : (
          <>
            {detectedUnpriced.length > 0 && (
              <div style={{
                marginBottom: '16px',
                padding: '12px 16px',
                borderRadius: 'var(--radius)',
                background: 'var(--status-warn-bg)',
                border: '1px solid var(--status-warn-fill)',
              }}>
                <div style={{ fontSize: '12px', fontWeight: '600', color: 'var(--status-warn-text)', marginBottom: '10px' }}>
                  Detected unpriced models
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                  {detectedUnpriced.map(model => (
                    <div key={model} style={{
                      display: 'flex', alignItems: 'center', gap: '6px',
                      background: 'var(--surface)', borderRadius: 'var(--radius)',
                      padding: '4px 10px', border: '1px solid var(--status-warn-fill)',
                    }}>
                      <span style={{ fontFamily: 'var(--font-mono)', fontSize: '12px', color: 'var(--ink-2)' }}>
                        {model}
                      </span>
                      <button
                        type="button"
                        onClick={() => handleQuickAddModel(model)}
                        style={{
                          background: 'var(--accent)', border: 'none', color: '#fff',
                          borderRadius: '4px', padding: '2px 8px', fontSize: '11px',
                          fontWeight: '600', cursor: 'pointer', lineHeight: '1.6',
                        }}
                      >
                        + Add
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div className="custom-table-container" style={{ marginBottom: '12px' }}>
              <table className="custom-table">
                <thead>
                  <tr>
                    <th>Model ID</th>
                    <th>Input ≤200K $/1M</th>
                    <th>Input &gt;200K $/1M</th>
                    <th>Output $/1M</th>
                    <th>Non-global ×</th>
                    <th style={{ width: '56px' }}></th>
                  </tr>
                </thead>
                <tbody>
                  {Object.keys(editedPricing).length === 0 && (
                    <tr>
                      <td colSpan={6} style={{ textAlign: 'center', color: 'var(--ink-3)', padding: '24px', fontStyle: 'italic' }}>
                        No pricing configured — use the row below to add a model.
                      </td>
                    </tr>
                  )}
                  {Object.entries(editedPricing).map(([modelId, rates]) => (
                    <tr key={modelId}>
                      <td style={{ fontFamily: 'var(--font-mono)', fontSize: '13px' }}>{modelId}</td>
                      <td>
                        <input
                          type="number"
                          className="form-control"
                          value={rates.input_cost_per_million}
                          onChange={(e) => handleRateChange(modelId, 'input_cost_per_million', e.target.value)}
                          min="0"
                          step="0.001"
                          style={rateInputStyle}
                        />
                      </td>
                      <td>
                        <input
                          type="number"
                          className="form-control"
                          value={rates.input_cost_per_million_gt_200k ?? ''}
                          onChange={(e) => handleRateChange(modelId, 'input_cost_per_million_gt_200k', e.target.value)}
                          min="0"
                          step="0.001"
                          placeholder="= base"
                          style={rateInputStyle}
                        />
                      </td>
                      <td>
                        <input
                          type="number"
                          className="form-control"
                          value={rates.output_cost_per_million}
                          onChange={(e) => handleRateChange(modelId, 'output_cost_per_million', e.target.value)}
                          min="0"
                          step="0.001"
                          style={rateInputStyle}
                        />
                      </td>
                      <td>
                        <input
                          type="number"
                          className="form-control"
                          value={rates.non_global_multiplier ?? ''}
                          onChange={(e) => handleRateChange(modelId, 'non_global_multiplier', e.target.value)}
                          min="1"
                          max="3"
                          step="0.01"
                          placeholder="1.0"
                          style={rateInputStyle}
                        />
                      </td>
                      <td style={{ textAlign: 'center' }}>
                        <button
                          onClick={() => handleDeleteModel(modelId)}
                          title="Remove model"
                          style={{
                            background: 'none', border: 'none', cursor: 'pointer',
                            color: 'var(--ink-3)', padding: '4px 8px', borderRadius: '4px',
                            fontSize: '14px', transition: 'color 0.15s, background-color 0.15s',
                          }}
                          onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--status-crit)'; e.currentTarget.style.backgroundColor = 'var(--status-crit-bg)'; }}
                          onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--ink-3)'; e.currentTarget.style.backgroundColor = 'transparent'; }}
                        >
                          🗑️
                        </button>
                      </td>
                    </tr>
                  ))}
                  {/* Add-model row */}
                  <tr style={{ backgroundColor: 'var(--surface-2)' }}>
                    <td>
                      <input
                        type="text"
                        className="form-control"
                        placeholder="new-model-id"
                        value={newModelId}
                        onChange={(e) => { setNewModelId(e.target.value); setNewModelError(''); }}
                        style={{ padding: '6px 10px' }}
                      />
                      {newModelError && (
                        <div style={{ fontSize: '11px', color: 'var(--status-crit)', marginTop: '4px' }}>
                          {newModelError}
                        </div>
                      )}
                    </td>
                    <td>
                      <input
                        type="number"
                        className="form-control"
                        placeholder="0.000"
                        value={newInputRate}
                        onChange={(e) => setNewInputRate(e.target.value)}
                        min="0"
                        step="0.001"
                        style={rateInputStyle}
                      />
                    </td>
                    <td>
                      <input
                        type="number"
                        className="form-control"
                        placeholder="= base"
                        value={newGt200kRate}
                        onChange={(e) => setNewGt200kRate(e.target.value)}
                        min="0"
                        step="0.001"
                        style={rateInputStyle}
                      />
                    </td>
                    <td>
                      <input
                        type="number"
                        className="form-control"
                        placeholder="0.000"
                        value={newOutputRate}
                        onChange={(e) => setNewOutputRate(e.target.value)}
                        min="0"
                        step="0.001"
                        style={rateInputStyle}
                      />
                    </td>
                    <td>
                      <input
                        type="number"
                        className="form-control"
                        placeholder="1.0"
                        value={newMultiplier}
                        onChange={(e) => setNewMultiplier(e.target.value)}
                        min="1"
                        max="3"
                        step="0.01"
                        style={rateInputStyle}
                      />
                    </td>
                    <td style={{ textAlign: 'center' }}>
                      <button
                        onClick={handleAddModel}
                        title="Add model"
                        style={{
                          background: 'none', border: 'none', cursor: 'pointer',
                          color: 'var(--accent)', padding: '4px 8px', borderRadius: '4px',
                          fontSize: '18px', fontWeight: '700', lineHeight: '1',
                          transition: 'background-color 0.15s',
                        }}
                        onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = 'var(--accent-soft)'; }}
                        onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = 'transparent'; }}
                      >
                        ＋
                      </button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            {/* Table caption */}
            <p style={{ fontSize: '11.5px', color: 'var(--ink-3)', marginBottom: '16px', lineHeight: '1.55' }}>
              Rates are Standard-tier, global-endpoint prices. Non-global × applies a regional premium to both input and output.
              The &gt;200K rate applies per request when its input exceeds 200K tokens.
            </p>

            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '16px', flexWrap: 'wrap' }}>
              <p style={{ fontSize: '12px', color: 'var(--ink-3)', margin: 0 }}>
                ⚠ Saved rates immediately affect cost calculations across the entire dashboard.
              </p>
              <button
                className="btn-primary"
                onClick={handleSavePricing}
                disabled={pricingSaving || Object.keys(editedPricing).length === 0}
              >
                {pricingSaving ? 'Saving...' : 'Save pricing'}
              </button>
            </div>
          </>
        )}
      </div>

      {/* ═══ Section 2: Financial Planner ═══════════════════════════════════════ */}
      <div className="card-panel" style={{ minHeight: 'auto' }}>
        <h3 className="panel-title" style={{ borderBottom: '1px solid var(--line)', paddingBottom: '16px', marginBottom: '20px' }}>
          📊 Financial Planner
        </h3>

        <Notification notif={estimatesNotif} />

        <div className="planner-grid">
          {/* ── Left: Estimate builder ── */}
          <div>
            {/* Name */}
            <div className="form-group">
              <label>Estimate Name</label>
              <input
                type="text"
                className="form-control"
                placeholder="e.g. Q3 Production Workloads"
                value={builderName}
                onChange={(e) => { setBuilderName(e.target.value); setBuilderErrors(prev => ({ ...prev, name: undefined })); }}
                maxLength={100}
              />
              {builderErrors.name && (
                <div style={{ fontSize: '11px', color: 'var(--status-crit)', marginTop: '2px' }}>{builderErrors.name}</div>
              )}
            </div>

            {/* Line items */}
            {builderErrors.items && (
              <div style={{ fontSize: '12px', color: 'var(--status-crit)', marginBottom: '10px' }}>{builderErrors.items}</div>
            )}

            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', marginBottom: '14px' }}>
              {builderItems.map((item, idx) => {
                const lineData = builderTotals.perLine[idx];
                /* Collect model options: all pricing models + any missing model this line references */
                const optionModels = [...modelIds];
                if (item.model && !editedPricing[item.model] && !optionModels.includes(item.model)) {
                  optionModels.unshift(item.model);
                }

                const modelRates = editedPricing[item.model];
                const hasGt200k = modelRates &&
                  modelRates.input_cost_per_million_gt_200k !== undefined &&
                  modelRates.input_cost_per_million_gt_200k !== null &&
                  modelRates.input_cost_per_million_gt_200k !== '';
                const hasMultiplier = modelRates &&
                  modelRates.non_global_multiplier !== undefined &&
                  modelRates.non_global_multiplier !== null &&
                  modelRates.non_global_multiplier !== '';

                return (
                  <div
                    key={idx}
                    style={{
                      background: 'var(--surface-2)',
                      border: '1px solid var(--line)',
                      borderRadius: 'var(--radius)',
                      padding: '14px 16px',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '10px',
                    }}
                  >
                    {/* Row 1: model, tokens, term, remove */}
                    <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', alignItems: 'flex-end' }}>
                      {/* Model select */}
                      <div style={{ flex: '1 1 160px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '5px' }}>
                          <span style={{ fontSize: '11px', fontWeight: '500', color: 'var(--ink-2)' }}>Model</span>
                          {lineData?.missingModel && (
                            <span
                              title="Model not in pricing table — rates treated as $0"
                              style={{ fontSize: '10px', color: 'var(--status-warn)', fontWeight: '600' }}
                            >
                              ⚠ not in pricing
                            </span>
                          )}
                        </div>
                        <select
                          className="form-control"
                          value={item.model}
                          onChange={(e) => updateBuilderItem(idx, 'model', e.target.value)}
                          style={{ padding: '6px 10px' }}
                        >
                          {optionModels.length === 0 ? (
                            <option value="">No models — add pricing first</option>
                          ) : (
                            optionModels.map(m => (
                              <option key={m} value={m}>
                                {m}{!editedPricing[m] ? ' ⚠' : ''}
                              </option>
                            ))
                          )}
                        </select>
                      </div>

                      {/* Input tokens */}
                      <div style={{ flex: '1 1 110px' }}>
                        <div style={{ fontSize: '11px', fontWeight: '500', color: 'var(--ink-2)', marginBottom: '5px' }}>Input tokens / term</div>
                        <input
                          type="number"
                          className="form-control"
                          value={item.input_tokens}
                          onChange={(e) => updateBuilderItem(idx, 'input_tokens', e.target.value)}
                          min="0"
                          style={{ padding: '6px 10px' }}
                        />
                        {builderErrors[`item_${idx}_input`] && (
                          <div style={{ fontSize: '11px', color: 'var(--status-crit)', marginTop: '2px' }}>{builderErrors[`item_${idx}_input`]}</div>
                        )}
                      </div>

                      {/* Output tokens */}
                      <div style={{ flex: '1 1 110px' }}>
                        <div style={{ fontSize: '11px', fontWeight: '500', color: 'var(--ink-2)', marginBottom: '5px' }}>Output tokens / term</div>
                        <input
                          type="number"
                          className="form-control"
                          value={item.output_tokens}
                          onChange={(e) => updateBuilderItem(idx, 'output_tokens', e.target.value)}
                          min="0"
                          style={{ padding: '6px 10px' }}
                        />
                        {builderErrors[`item_${idx}_output`] && (
                          <div style={{ fontSize: '11px', color: 'var(--status-crit)', marginTop: '2px' }}>{builderErrors[`item_${idx}_output`]}</div>
                        )}
                      </div>

                      {/* Term */}
                      <div style={{ flex: '0 0 100px' }}>
                        <div style={{ fontSize: '11px', fontWeight: '500', color: 'var(--ink-2)', marginBottom: '5px' }}>Term</div>
                        <select
                          className="form-control"
                          value={item.term}
                          onChange={(e) => updateBuilderItem(idx, 'term', e.target.value)}
                          style={{ padding: '6px 10px' }}
                        >
                          <option value="day">Day</option>
                          <option value="week">Week</option>
                          <option value="month">Month</option>
                          <option value="year">Year</option>
                        </select>
                      </div>

                      {/* Remove button */}
                      <button
                        onClick={() => removeBuilderItem(idx)}
                        title="Remove line"
                        style={{
                          background: 'none', border: 'none', cursor: 'pointer',
                          color: 'var(--ink-3)', padding: '6px 8px', borderRadius: '4px',
                          fontSize: '14px', alignSelf: 'flex-end', transition: 'color 0.15s',
                          marginBottom: '1px',
                        }}
                        onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--status-crit)'; }}
                        onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--ink-3)'; }}
                      >
                        ✕
                      </button>
                    </div>

                    {/* Row 2: Region + Long-context (UI-only calculation settings) */}
                    <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap', alignItems: 'center' }}>
                      {/* Region select */}
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <span style={{ fontSize: '11px', fontWeight: '500', color: 'var(--ink-2)', whiteSpace: 'nowrap' }}>Region</span>
                        <select
                          className="form-control"
                          value={item.region}
                          onChange={(e) => updateBuilderItem(idx, 'region', e.target.value)}
                          style={{ padding: '5px 8px', fontSize: '12px', width: 'auto' }}
                        >
                          <option value="global">Global</option>
                          <option value="regional">Regional (non-global)</option>
                        </select>
                        {item.region === 'regional' && !hasMultiplier && (
                          <span
                            title="This model has no non_global_multiplier — no regional premium applied"
                            style={{ fontSize: '10px', color: 'var(--status-warn)', fontWeight: '600' }}
                          >
                            ⚠ no ×
                          </span>
                        )}
                      </div>

                      {/* Long context checkbox */}
                      <label style={{ display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer', userSelect: 'none' }}>
                        <input
                          type="checkbox"
                          checked={item.longContext}
                          onChange={(e) => updateBuilderItem(idx, 'longContext', e.target.checked)}
                          style={{ width: '14px', height: '14px', accentColor: 'var(--accent)', cursor: 'pointer' }}
                        />
                        <span style={{ fontSize: '11px', fontWeight: '500', color: 'var(--ink-2)', whiteSpace: 'nowrap' }}>
                          Long context (&gt;200K input/request)
                        </span>
                        {item.longContext && !hasGt200k && (
                          <span
                            title="This model has no >200K rate — base input rate used instead"
                            style={{ fontSize: '10px', color: 'var(--status-warn)', fontWeight: '600' }}
                          >
                            ⚠ no &gt;200K rate
                          </span>
                        )}
                      </label>
                    </div>

                    {/* Note */}
                    <input
                      type="text"
                      className="form-control"
                      placeholder="What drives this consumption? e.g. nightly batch summarization"
                      value={item.note}
                      onChange={(e) => updateBuilderItem(idx, 'note', e.target.value)}
                      maxLength={500}
                      style={{ padding: '6px 10px' }}
                    />
                    {builderErrors[`item_${idx}_note`] && (
                      <div style={{ fontSize: '11px', color: 'var(--status-crit)' }}>{builderErrors[`item_${idx}_note`]}</div>
                    )}

                    {/* Per-line live cost */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12px', flexWrap: 'wrap' }}>
                      <span style={{ color: 'var(--ink-3)' }}>Cost per {item.term}:</span>
                      <span style={{
                        fontFamily: 'var(--font-mono)', fontWeight: '600',
                        color: lineData?.missingModel ? 'var(--status-warn)' : 'var(--ink)',
                      }}>
                        {formatCurrency(lineData?.cost || 0)}
                        {lineData?.missingModel && (
                          <span title="Model not in pricing table — rates treated as $0"> ⚠</span>
                        )}
                      </span>
                      <span style={{ color: 'var(--ink-3)' }}>≈</span>
                      <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--ink-2)' }}>
                        {formatCurrency(lineData?.monthly || 0)} / mo
                      </span>
                      {(item.region === 'regional' || item.longContext) && (
                        <span style={{ fontSize: '10px', color: 'var(--ink-3)', fontStyle: 'italic' }}>
                          ({[item.region === 'regional' ? 'regional' : null, item.longContext ? '>200K' : null].filter(Boolean).join(', ')})
                        </span>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>

            <button
              className="btn-secondary"
              onClick={addBuilderLine}
              style={{ width: '100%', marginBottom: '20px', justifyContent: 'center' }}
            >
              + Add model line
            </button>

            {/* Summary panel */}
            {builderItems.length > 0 && (
              <div style={{
                background: 'var(--accent-soft)',
                border: '1px solid var(--accent)',
                borderRadius: 'var(--radius)',
                padding: '16px 20px',
                marginBottom: '16px',
              }}>
                <div style={{
                  fontSize: '11px', fontWeight: '700', color: 'var(--accent-text)',
                  marginBottom: '14px', textTransform: 'uppercase', letterSpacing: '0.07em',
                }}>
                  Estimate Summary
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '12px', marginBottom: builderItems.length > 1 ? '14px' : '0' }}>
                  {[
                    { label: 'Daily', value: builderTotals.daily },
                    { label: 'Monthly', value: builderTotals.monthly },
                    { label: 'Yearly', value: builderTotals.yearly },
                  ].map(({ label, value }) => (
                    <div key={label} style={{ textAlign: 'center' }}>
                      <div style={{ fontSize: '11px', color: 'var(--ink-3)', marginBottom: '4px' }}>{label}</div>
                      <div style={{ fontFamily: 'var(--font-mono)', fontWeight: '700', fontSize: '15px', color: 'var(--ink)' }}>
                        {formatCurrency(value)}
                      </div>
                    </div>
                  ))}
                </div>

                {builderItems.length > 1 && (
                  <div style={{ borderTop: '1px solid var(--line)', paddingTop: '12px' }}>
                    <div style={{ fontSize: '11px', color: 'var(--ink-3)', marginBottom: '6px', fontWeight: '600' }}>
                      Monthly by line:
                    </div>
                    {builderItems.map((item, idx) => {
                      const lineData = builderTotals.perLine[idx];
                      const label = item.note
                        ? `${item.model} · ${item.note.slice(0, 40)}${item.note.length > 40 ? '…' : ''}`
                        : item.model || '—';
                      return (
                        <div key={idx} style={{ display: 'flex', justifyContent: 'space-between', gap: '8px', padding: '2px 0', fontSize: '12px' }}>
                          <span style={{ color: 'var(--ink-2)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>
                            {label}
                            {lineData?.missingModel && (
                              <span title="Model not in pricing table" style={{ color: 'var(--status-warn)' }}> ⚠</span>
                            )}
                          </span>
                          <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--ink)', fontWeight: '600', flexShrink: 0 }}>
                            {formatCurrency(lineData?.monthly || 0)}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            )}

            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
              <p style={{ fontSize: '11px', color: 'var(--ink-3)', margin: 0, fontStyle: 'italic' }}>
                Region and context settings are not saved with estimates.
              </p>
              <button className="btn-primary" onClick={handleSaveEstimate} disabled={estimateSaving}>
                {estimateSaving ? 'Saving...' : 'Save estimate'}
              </button>
            </div>
          </div>

          {/* ── Right: Saved estimates list ── */}
          <div>
            <h4 style={{ fontSize: '14px', fontWeight: '600', color: 'var(--ink-2)', marginBottom: '14px' }}>
              Saved Estimates
            </h4>

            {estimatesLoading ? (
              <div style={{ color: 'var(--ink-3)', fontSize: '13px' }}>Loading estimates...</div>
            ) : Object.keys(estimates).length === 0 ? (
              <div style={{
                background: 'var(--surface-2)', border: '1px solid var(--line)',
                borderRadius: 'var(--radius)', padding: '24px', textAlign: 'center',
                color: 'var(--ink-3)', fontSize: '13px',
              }}>
                No estimates saved yet. Build one on the left and save it.
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                {Object.values(estimates).map((est) => {
                  const totals = getSavedEstimateTotals(est, editedPricing);
                  const isActive = builderName === est.name;
                  return (
                    <div
                      key={est.name}
                      onClick={() => handleLoadEstimate(est)}
                      style={{
                        background: isActive ? 'var(--accent-soft)' : 'var(--surface-2)',
                        border: `1px solid ${isActive ? 'var(--accent)' : 'var(--line)'}`,
                        borderRadius: 'var(--radius)', padding: '14px 16px', cursor: 'pointer',
                        transition: 'border-color 0.15s, background-color 0.15s',
                      }}
                      onMouseEnter={(e) => {
                        if (!isActive) {
                          e.currentTarget.style.borderColor = 'var(--accent)';
                          e.currentTarget.style.backgroundColor = 'var(--plane)';
                        }
                      }}
                      onMouseLeave={(e) => {
                        if (!isActive) {
                          e.currentTarget.style.borderColor = 'var(--line)';
                          e.currentTarget.style.backgroundColor = 'var(--surface-2)';
                        }
                      }}
                    >
                      {/* Header row */}
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '8px', marginBottom: '10px' }}>
                        <span style={{ fontWeight: '600', color: 'var(--ink)', fontSize: '13px', flex: 1, wordBreak: 'break-word' }}>
                          {est.name}
                        </span>
                        <button
                          onClick={(e) => { e.stopPropagation(); handleDeleteEstimate(est.name); }}
                          title="Delete estimate"
                          style={{
                            background: 'none', border: 'none', cursor: 'pointer',
                            color: 'var(--ink-3)', padding: '2px 6px', borderRadius: '4px',
                            fontSize: '13px', flexShrink: 0, transition: 'color 0.15s',
                          }}
                          onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--status-crit)'; }}
                          onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--ink-3)'; }}
                        >
                          🗑️
                        </button>
                      </div>

                      {/* Monthly / yearly figures */}
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px', marginBottom: '10px' }}>
                        {[
                          { label: 'Monthly', value: totals.monthly },
                          { label: 'Yearly', value: totals.yearly },
                        ].map(({ label, value }) => (
                          <div key={label}>
                            <div style={{ fontSize: '10px', color: 'var(--ink-3)', marginBottom: '2px' }}>{label}</div>
                            <div style={{ fontFamily: 'var(--font-mono)', fontWeight: '700', fontSize: '14px', color: 'var(--ink)' }}>
                              {formatCurrency(value)}
                            </div>
                          </div>
                        ))}
                      </div>

                      {/* Footer */}
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '11px', color: 'var(--ink-3)' }}>
                        <span>{(est.items || []).length} line{(est.items || []).length !== 1 ? 's' : ''}</span>
                        <span>{formatDateTime(est.updated_at)}</span>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
