import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import CostAnomalyBanner from '../components/CostAnomalyBanner.jsx';

function renderBanner(anomaly, onDismiss = vi.fn()) {
  return render(<CostAnomalyBanner anomaly={anomaly} onDismiss={onDismiss} />);
}

// ─── Non-rendering cases ──────────────────────────────────────────────────────

describe('CostAnomalyBanner — renders nothing', () => {
  it('renders nothing when anomaly is null', () => {
    const { container } = renderBanner(null);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing when anomaly is undefined', () => {
    const { container } = renderBanner(undefined);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing when is_anomaly is false', () => {
    const { container } = renderBanner({
      is_anomaly: false,
      today_cost_usd: 10,
      baseline_daily_avg_usd: 5,
      ratio: 2,
      baseline_days: 30,
      per_user: [],
    });
    expect(container).toBeEmptyDOMElement();
  });
});

// ─── Spike copy ───────────────────────────────────────────────────────────────

describe('CostAnomalyBanner — spike content', () => {
  const baseAnomaly = {
    is_anomaly: true,
    today_cost_usd: 25.0,
    baseline_daily_avg_usd: 5.0,
    ratio: 5.0,
    baseline_days: 14,
    per_user: [],
  };

  it('renders the formatted today cost', () => {
    renderBanner(baseAnomaly);
    // formatCurrency(25.0) → '$25.00'
    expect(screen.getByText(/\$25\.00/)).toBeInTheDocument();
  });

  it('renders the ratio and baseline average', () => {
    renderBanner(baseAnomaly);
    // Ratio text includes "5.0×" and the baseline daily avg
    expect(screen.getByText(/5\.0×/)).toBeInTheDocument();
    expect(screen.getByText(/\$5\.00/)).toBeInTheDocument();
  });

  it('renders the baseline days count', () => {
    renderBanner(baseAnomaly);
    expect(screen.getByText(/14 days/)).toBeInTheDocument();
  });

  it('renders anomalous user details when per_user has anomalies', () => {
    renderBanner({
      ...baseAnomaly,
      per_user: [
        { user_email: 'hog@example.com', is_anomaly: true, ratio: 8.5 },
        { user_email: 'quiet@example.com', is_anomaly: false, ratio: null },
      ],
    });
    expect(screen.getByText(/hog@example\.com/)).toBeInTheDocument();
    expect(screen.getByText(/8\.5×/)).toBeInTheDocument();
    // Non-anomalous user must not appear.
    expect(screen.queryByText(/quiet@example\.com/)).not.toBeInTheDocument();
  });
});

// ─── Dismiss button ───────────────────────────────────────────────────────────

describe('CostAnomalyBanner — dismiss button', () => {
  it('calls onDismiss when the dismiss button is clicked', async () => {
    const user = userEvent.setup();
    const onDismiss = vi.fn();
    renderBanner(
      {
        is_anomaly: true,
        today_cost_usd: 10,
        baseline_daily_avg_usd: 2,
        ratio: 5,
        baseline_days: 7,
        per_user: [],
      },
      onDismiss,
    );

    await user.click(screen.getByRole('button', { name: /dismiss cost spike/i }));
    expect(onDismiss).toHaveBeenCalledOnce();
  });
});

// ─── Null ratio edge case ─────────────────────────────────────────────────────

describe('CostAnomalyBanner — null ratio', () => {
  it('renders without crashing when ratio is null', () => {
    expect(() =>
      renderBanner({
        is_anomaly: true,
        today_cost_usd: 10,
        baseline_daily_avg_usd: 2,
        ratio: null,
        baseline_days: 7,
        per_user: [],
      }),
    ).not.toThrow();
  });

  it('shows the "vs." fallback text instead of a ratio when ratio is null', () => {
    renderBanner({
      is_anomaly: true,
      today_cost_usd: 10,
      baseline_daily_avg_usd: 2,
      ratio: null,
      baseline_days: 7,
      per_user: [],
    });
    expect(screen.getByText(/vs\./)).toBeInTheDocument();
    // Must not render "×" since ratio is null.
    expect(screen.queryByText(/×/)).not.toBeInTheDocument();
  });
});
