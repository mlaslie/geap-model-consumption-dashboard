import React, { useState } from 'react';
import { formatCurrency, formatTokens } from '../utils/formatters';

export default function BudgetManager({ budgets, onSaveBudgets }) {
  const [selectedIdentity, setSelectedIdentity] = useState('global_default');
  const [period, setPeriod] = useState('month');
  const [type, setType] = useState('token');
  const [limit, setLimit] = useState(10000000);
  const [alertThreshold, setAlertThreshold] = useState(50);
  const [hardLimit, setHardLimit] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [customIdentity, setCustomIdentity] = useState('');
  const [showCustomInput, setShowCustomInput] = useState(false);

  // Handle changing select principal dropdown
  const handleIdentityChange = (val) => {
    if (val === 'custom') {
      setShowCustomInput(true);
      setSelectedIdentity('');
      // Set defaults for custom user
      setPeriod('month');
      setType('money');
      setLimit(50);
      setAlertThreshold(80);
      setHardLimit(false);
    } else {
      setShowCustomInput(false);
      setSelectedIdentity(val);
      // Load existing configuration if any
      const existing = budgets[val];
      if (existing) {
        setPeriod(existing.period || 'month');
        setType(existing.type || 'token');
        setLimit(existing.limit || 1000000);
        setAlertThreshold(existing.alert_threshold_percentage || 50);
        setHardLimit(existing.hard_limit_enabled || false);
      }
    }
  };

  // Handle clicking a budget rule item in the list to load it
  const handleSelectRule = (key) => {
    setShowCustomInput(false);
    setSelectedIdentity(key);
    const existing = budgets[key];
    if (existing) {
      setPeriod(existing.period || 'month');
      setType(existing.type || 'token');
      setLimit(existing.limit || 1000000);
      setAlertThreshold(existing.alert_threshold_percentage || 50);
      setHardLimit(existing.hard_limit_enabled || false);
    }
  };

  // Handle deleting custom budget rule
  const handleDeleteBudget = async (identityToDelete) => {
    if (identityToDelete === 'global_default') {
      alert("The global default budget cannot be deleted. You can modify its limits instead.");
      return;
    }
    
    if (!window.confirm(`Are you sure you want to delete the budget constraint for '${identityToDelete}'?`)) {
      return;
    }
    
    setIsSaving(true);
    const updatedBudgets = { ...budgets };
    delete updatedBudgets[identityToDelete];
    
    const success = await onSaveBudgets(updatedBudgets);
    if (success) {
      // Revert to global default
      setSelectedIdentity('global_default');
      setShowCustomInput(false);
      const existing = updatedBudgets['global_default'];
      if (existing) {
        setPeriod(existing.period || 'month');
        setType(existing.type || 'token');
        setLimit(existing.limit || 1000000);
        setAlertThreshold(existing.alert_threshold_percentage || 50);
        setHardLimit(existing.hard_limit_enabled || false);
      }
    }
    setIsSaving(false);
  };

  // Submit and save configuration
  const handleSubmit = async (e) => {
    e.preventDefault();
    setIsSaving(true);
    
    const identityToSave = showCustomInput ? customIdentity : selectedIdentity;
    if (!identityToSave) {
      alert("Please enter a valid User/Service Account email.");
      setIsSaving(false);
      return;
    }

    const updatedBudgets = {
      ...budgets,
      [identityToSave]: {
        identity: identityToSave,
        period,
        type,
        limit: Number(limit),
        alert_threshold_percentage: Number(alertThreshold),
        hard_limit_enabled: hardLimit
      }
    };

    const success = await onSaveBudgets(updatedBudgets);
    if (success) {
      // Reset custom state
      if (showCustomInput) {
        setShowCustomInput(false);
        setSelectedIdentity(customIdentity);
        setCustomIdentity('');
      }
    }
    setIsSaving(false);
  };

  return (
    <div className="card-panel" style={{ minHeight: '500px' }}>
      <h3 className="panel-title" style={{ borderBottom: '1px solid var(--border-color)', paddingBottom: '16px' }}>
        <span style={{color: 'var(--accent-indigo)'}}>💼</span> Budget and Threshold Manager
      </h3>

      <div className="budget-form-container">
        {/* Left Form Panel */}
        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <div className="form-group">
            <label>Select Principal Target</label>
            <select 
              className="form-control" 
              value={showCustomInput ? 'custom' : selectedIdentity}
              onChange={(e) => handleIdentityChange(e.target.value)}
            >
              <option value="global_default">Global Organization Default</option>
              {Object.keys(budgets).filter(k => k !== 'global_default').map(key => (
                <option key={key} value={key}>{key}</option>
              ))}
              <option value="custom">+ Assign Budget to custom User/SA...</option>
            </select>
          </div>

          {showCustomInput && (
            <div className="form-group">
              <label>Enter Principal Email / Service Account</label>
              <input 
                type="email" 
                placeholder="e.g. researcher@your-domain.com"
                className="form-control"
                value={customIdentity}
                onChange={(e) => setCustomIdentity(e.target.value)}
                required
              />
            </div>
          )}

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
            <div className="form-group">
              <label>Budget Period</label>
              <select className="form-control" value={period} onChange={(e) => setPeriod(e.target.value)}>
                <option value="day">Daily</option>
                <option value="week">Weekly</option>
                <option value="month">Monthly</option>
                <option value="year">Yearly</option>
              </select>
            </div>

            <div className="form-group">
              <label>Budget Metric</label>
              <select className="form-control" value={type} onChange={(e) => setType(e.target.value)}>
                <option value="token">Tokens (Input + Output)</option>
                <option value="money">Financial Spend (USD $)</option>
              </select>
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
            <div className="form-group">
              <label>Budget Limit ({type === 'money' ? 'USD $' : 'Tokens'})</label>
              <input 
                type="number" 
                className="form-control"
                value={limit}
                onChange={(e) => setLimit(e.target.value)}
                required
                min="0.1"
                step={type === 'money' ? '0.01' : '1'}
              />
            </div>

            <div className="form-group">
              <label>Alert Trigger Threshold (%)</label>
              <input 
                type="number" 
                className="form-control"
                value={alertThreshold}
                onChange={(e) => setAlertThreshold(e.target.value)}
                required
                min="1"
                max="100"
              />
            </div>
          </div>

          <div className="form-group checkbox-group">
            <input 
              type="checkbox" 
              id="hard-limit-check" 
              checked={hardLimit} 
              onChange={(e) => setHardLimit(e.target.checked)}
            />
            <label htmlFor="hard-limit-check" style={{ cursor: 'pointer' }}>
              Flag for hard budget block (enforcement planned — not yet active)
            </label>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
            <button type="submit" className="btn-primary" disabled={isSaving}>
              {isSaving ? 'Saving parameters to GCS...' : 'Apply Budget Configuration'}
            </button>

            {selectedIdentity !== 'global_default' && !showCustomInput && (
              <button 
                type="button" 
                className="btn-secondary" 
                onClick={() => handleDeleteBudget(selectedIdentity)}
                style={{ 
                  borderColor: 'var(--status-crit)',
                  color: 'var(--status-crit)',
                  backgroundColor: 'var(--status-crit-bg)',
                  width: '100%'
                }}
                disabled={isSaving}
              >
                Delete Budget Rule
              </button>
            )}
          </div>
        </form>

        {/* Right Active Rules Summary Panel */}
        <div className="budget-list-section" style={{ borderRight: 'none', paddingRight: '0' }}>
          <h4 style={{ fontSize: '14px', color: 'var(--text-secondary)', marginBottom: '16px', fontWeight: '600' }}>
            Current Active Budgeting Constraints
          </h4>
          <div style={{ maxHeight: '350px', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '12px' }}>
            {Object.entries(budgets).map(([key, value]) => {
              const isSelected = !showCustomInput && selectedIdentity === key;
              return (
                <div 
                  key={key} 
                  className={`budget-rule-item ${isSelected ? 'selected' : ''}`}
                  onClick={() => handleSelectRule(key)}
                  style={{
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    padding: '12px 16px',
                    borderRadius: '10px',
                    border: isSelected ? '1px solid var(--accent-indigo)' : '1px solid var(--border-color)',
                    background: isSelected ? 'var(--accent-soft)' : 'var(--surface-2)',
                    transition: 'all 0.2s ease',
                  }}
                  onMouseEnter={(e) => {
                    if (!isSelected) {
                      e.currentTarget.style.borderColor = 'var(--accent)';
                      e.currentTarget.style.background = 'var(--plane)';
                    }
                  }}
                  onMouseLeave={(e) => {
                    if (!isSelected) {
                      e.currentTarget.style.borderColor = 'var(--border-color)';
                      e.currentTarget.style.background = 'var(--surface-2)';
                    }
                  }}
                >
                  <div className="rule-info" style={{ flex: 1 }}>
                    <h4 style={{ color: key === 'global_default' ? 'var(--accent)' : 'var(--ink)', margin: 0, fontSize: '13px', fontWeight: '600' }}>
                      {key === 'global_default' ? '🌐 GLOBAL DEFAULT' : `👤 ${key}`}
                    </h4>
                    <div className="rule-details" style={{ marginTop: '4px', fontSize: '11px', color: 'var(--text-secondary)' }}>
                      <span>Type: <strong>{value.type}</strong> | Period: <strong>{value.period}</strong> | Limit: <strong>
                        {value.type === 'money' ? formatCurrency(value.limit) : formatTokens(value.limit)}
                      </strong></span>
                    </div>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                    <span style={{ 
                      fontSize: '11px', 
                      padding: '3px 8px', 
                      borderRadius: '4px',
                      backgroundColor: 'var(--status-warn-bg)',
                      border: '1px solid var(--status-warn-fill)',
                      color: 'var(--status-warn-text)',
                      fontWeight: '600'
                    }}>
                      Alert at {value.alert_threshold_percentage}%
                    </span>
                    {key !== 'global_default' && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation(); // Avoid triggering row selection click
                          handleDeleteBudget(key);
                        }}
                        style={{
                          background: 'none',
                          border: 'none',
                          color: 'var(--text-secondary)',
                          fontSize: '14px',
                          cursor: 'pointer',
                          padding: '4px 8px',
                          borderRadius: '6px',
                          transition: 'all 0.2s',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center'
                        }}
                        onMouseEnter={(e) => {
                          e.target.style.color = 'var(--accent-rose)';
                          e.target.style.backgroundColor = 'var(--status-crit-bg)';
                        }}
                        onMouseLeave={(e) => {
                          e.target.style.color = 'var(--text-secondary)';
                          e.target.style.backgroundColor = 'transparent';
                        }}
                        title="Delete budget rule"
                      >
                        🗑️
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
