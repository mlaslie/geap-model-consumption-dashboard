import React, { useState, useRef, useEffect } from 'react';
import { apiFetch } from '../utils/api';

export default function FinOpsAssistant() {
  const [messages, setMessages] = useState([
    {
      role: 'model',
      content: 'Hello! I am your **Gemini FinOps Assistant** powered by `gemini-3.6-flash`. I am actively parsing live BigQuery telemetry logs and GCS budget files. \n\nAsk me any questions about cost spikes, model efficiencies, or budget alert violations!'
    }
  ]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const scrollRef = useRef(null);

  // Automatically scroll chat history to bottom on new messages
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, isLoading]);

  const handleSend = async (textToSend) => {
    const userText = textToSend || input;
    if (!userText.trim()) return;

    if (!textToSend) setInput('');

    // Append user message
    const updatedMessages = [...messages, { role: 'user', content: userText }];
    setMessages(updatedMessages);
    setIsLoading(true);

    try {
      const response = await apiFetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ messages: updatedMessages })
      });

      if (!response.ok) {
        let detail = response.statusText;
        try {
          const errData = await response.json();
          detail = errData.detail || detail;
        } catch { }
        throw new Error(detail);
      }

      const data = await response.json();
      setMessages([...updatedMessages, { role: 'model', content: data.reply }]);
    } catch (e) {
      setMessages([...updatedMessages, { 
        role: 'model', 
        content: `❌ **Error connecting to AI Copilot:** ${e.message}. Please verify your backend credentials or configuration.` 
      }]);
    } finally {
      setIsLoading(false);
    }
  };

  // Helper to convert simple markdown-style **bold** and ### headers into HTML elements safely
  const formatMarkdown = (text) => {
    if (!text) return '';
    return text.split('\n').map((line, idx) => {
      let content = line;
      
      // Parse headers
      if (content.startsWith('### ')) {
        return <h4 key={idx} style={{ color: 'var(--accent-indigo)', fontSize: '15px', fontWeight: '700', marginTop: '14px', marginBottom: '8px' }}>{content.replace('### ', '')}</h4>;
      }
      
      // Parse bold **text**
      const boldRegex = /\*\*(.*?)\*\*/g;
      const parts = [];
      let lastIndex = 0;
      let match;
      
      while ((match = boldRegex.exec(content)) !== null) {
        if (match.index > lastIndex) {
          parts.push(content.substring(lastIndex, match.index));
        }
        parts.push(<strong key={match.index} style={{ color: 'var(--ink)', fontWeight: '600' }}>{match[1]}</strong>);
        lastIndex = boldRegex.lastIndex;
      }
      
      if (lastIndex < content.length) {
        parts.push(content.substring(lastIndex));
      }
      
      return <p key={idx} style={{ marginBottom: '8px' }}>{parts.length > 0 ? parts : content}</p>;
    });
  };

  const suggestions = [
    { label: "👤 Spender Audit", query: "Who is the top token consumer? Audit their usage and budget position." },
    { label: "🚨 Budget Alerts", query: "Are any users exceeding set budget limits or alert thresholds?" },
    { label: "💡 Flash Savings", query: "Calculate cost savings switching from pro to gemini-3.6-flash" }
  ];

  return (
    <div className="assistant-panel">
      <div className="assistant-header">
        <span className="assistant-pulsar" />
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <span style={{ fontSize: '14px', fontWeight: '700', color: 'var(--ink)' }}>Gemini FinOps Assistant</span>
          <span style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>gemini-3.6-flash • global region</span>
        </div>
      </div>

      {/* Chat History */}
      <div className="assistant-chat-history" ref={scrollRef}>
        {messages.map((msg, idx) => (
          <div key={idx} className={`chat-bubble ${msg.role === 'user' ? 'user' : 'assistant'}`}>
            {formatMarkdown(msg.content)}
          </div>
        ))}
        {isLoading && (
          <div className="chat-bubble assistant" style={{ display: 'flex', alignItems: 'center', gap: '8px', color: 'var(--text-muted)' }}>
            <span className="assistant-pulsar" style={{ width: '6px', height: '6px' }} />
            <span>Analyzing BigQuery logs and generating optimization strategy...</span>
          </div>
        )}
      </div>

      {/* Suggestions and Input bar */}
      <div style={{ padding: '12px 22px 0 22px', backgroundColor: 'var(--surface-2)', borderTop: '1px solid var(--line)', display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
        {suggestions.map((s, idx) => (
          <button 
            key={idx} 
            className="btn-primary" 
            style={{ 
              fontSize: '11px', 
              padding: '6px 12px', 
              boxShadow: 'none', 
              backgroundColor: 'var(--surface)',
              color: 'var(--ink-2)',
              border: '1px solid var(--border-color)',
            }}
            onClick={() => handleSend(s.query)}
            disabled={isLoading}
          >
            {s.label}
          </button>
        ))}
      </div>

      <div className="assistant-chat-input-bar">
        <input 
          type="text" 
          placeholder="Ask about model usage, budget breaches, or saving insights..." 
          className="assistant-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleSend()}
          disabled={isLoading}
        />
        <button 
          className="btn-primary" 
          onClick={() => handleSend()}
          disabled={isLoading}
        >
          Send
        </button>
      </div>
    </div>
  );
}
