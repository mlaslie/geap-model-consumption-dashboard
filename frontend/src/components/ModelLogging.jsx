import React, { useState, useEffect } from 'react';
import { apiFetch } from '../utils/api';

export default function ModelLogging() {
  const [modelStates, setModelStates] = useState({
    "gemini-2.5-pro": false,
    "gemini-2.5-flash": false,
    "gemini-3.1-flash-lite": false,
    "gemini-3-flash-preview": false,
    "gemini-3.1-pro-preview": false,
    "gemini-3.5-flash": false,
    "gemini-3.6-flash": false
  });
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [notification, setNotification] = useState(null);

  useEffect(() => {
    fetchLoggingConfig();
  }, []);

  const fetchLoggingConfig = async () => {
    setIsLoading(true);
    try {
      const response = await apiFetch('/api/logging-config');
      const json = await response.json();
      if (!response.ok) {
        showNotification(`Failed to load configuration: ${json.detail || response.statusText}`, "error");
      } else if (json.status === 'success') {
        setModelStates(json.data);
      }
    } catch (e) {
      if (e.message !== 'Unauthorized') {
        console.error("Failed to load model logging configs:", e);
        showNotification("Failed to load configuration", "error");
      }
    } finally {
      setIsLoading(false);
    }
  };

  const showNotification = (message, type = 'success') => {
    setNotification({ message, type });
    setTimeout(() => setNotification(null), 5000);
  };

  const handleCheckboxChange = (modelId) => {
    setModelStates(prev => ({
      ...prev,
      [modelId]: !prev[modelId]
    }));
  };

  const handleApply = async () => {
    setIsSaving(true);
    try {
      const response = await apiFetch('/api/logging-config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(modelStates)
      });
      const json = await response.json();
      if (!response.ok) {
        showNotification(`Failed to save config: ${json.detail || response.statusText}`, 'error');
      } else if (json.status === 'success') {
        showNotification("Request-response logging successfully updated!");
      } else if (json.status === 'partial_failure') {
        const failed = Object.entries(json.results || {})
          .filter(([, r]) => !r.success)
          .map(([modelId, r]) => {
            const errMsg = r.error ? r.error.slice(0, 80) : 'unknown error';
            return `${modelId}: ${errMsg}`;
          });
        showNotification(`Partial failure — ${failed.join('; ')}`, 'error');
      } else {
        showNotification(json.message || json.detail || "Failed to save config", "error");
      }
    } catch (e) {
      if (e.message !== 'Unauthorized') {
        console.error("Failed to update logging configurations:", e);
        showNotification("Error connecting to Vertex AI SDK", "error");
      }
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="card-container" style={{ animation: 'fadeIn 0.5s ease-out' }}>
      <div className="card-header" style={{ marginBottom: '24px' }}>
        <h2 style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span>📡</span> Vertex AI Foundation Model Payload Logging
        </h2>
        <p style={{ color: 'var(--text-secondary)', fontSize: '13px', marginTop: '6px' }}>
          Dynamically toggle payload request-response logging directly into your BigQuery dataset `vertex_ai_user_telemetry` via the Vertex AI SDK.
        </p>
      </div>

      {notification && (
        <div style={{
          backgroundColor: notification.type === 'success' ? 'var(--accent-emerald-glow)' : 'var(--status-crit-bg)',
          border: `1px solid ${notification.type === 'success' ? 'var(--series-3)' : 'var(--status-crit)'}`,
          borderRadius: 'var(--radius)',
          padding: '12px 16px',
          color: notification.type === 'success' ? 'var(--status-good)' : 'var(--status-crit)',
          fontSize: '13px',
          fontWeight: '500',
          marginBottom: '20px',
          display: 'flex',
          alignItems: 'center',
          gap: '8px'
        }}>
          <span>{notification.type === 'success' ? '✅' : '⚠️'}</span>
          <span>{notification.message}</span>
        </div>
      )}

      {isLoading ? (
        <div style={{ padding: '24px 0', textAlign: 'center', color: 'var(--text-secondary)' }}>
          Loading Vertex foundation model statuses...
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
          <div style={{
            display: 'flex',
            flexDirection: 'column',
            gap: '12px',
            backgroundColor: 'var(--surface-2)',
            borderRadius: 'var(--radius)',
            border: '1px solid var(--line)',
            padding: '16px 20px'
          }}>
            {Object.keys(modelStates).map((modelId) => (
              <label 
                key={modelId}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  padding: '12px 16px',
                  borderRadius: '8px',
                  backgroundColor: 'var(--plane)',
                  border: '1px solid var(--line)',
                  cursor: 'pointer',
                  transition: 'all 0.2s ease'
                }}
                className="hover-bright"
              >
                <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
                  <span style={{ fontSize: '14px', fontWeight: '600', color: 'var(--ink)', fontFamily: 'var(--font-mono)' }}>
                    {modelId}
                  </span>
                  <span style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>
                    Foundation model deployed in global region
                  </span>
                </div>
                <input 
                  type="checkbox" 
                  checked={modelStates[modelId]}
                  onChange={() => handleCheckboxChange(modelId)}
                  style={{
                    width: '18px',
                    height: '18px',
                    borderRadius: '4px',
                    accentColor: 'var(--accent-teal)',
                    cursor: 'pointer'
                  }}
                />
              </label>
            ))}
          </div>

          <div style={{
            backgroundColor: 'var(--accent-soft)',
            border: '1px solid var(--accent)',
            borderRadius: 'var(--radius)',
            padding: '12px 16px',
            fontSize: '12px',
            color: 'var(--accent)',
            lineHeight: '1.5'
          }}>
            <strong>💡 How this works:</strong> Enabling logging calls the preview <code>set_request_response_logging_config</code> function in the Vertex AI SDK. Raw input prompts, generated responses, and exact token counts are routed directly into the <code>request_response_logging</code> table in BigQuery, making them instantly available in the dashboard!
          </div>

          <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '12px' }}>
            <button 
              className="btn-primary" 
              onClick={handleApply} 
              disabled={isSaving}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                padding: '10px 24px'
              }}
            >
              {isSaving ? (
                <>
                  <span className="assistant-pulsar" style={{ width: '12px', height: '12px' }} />
                  Applying configs...
                </>
              ) : (
                'Apply Logging Settings'
              )}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
