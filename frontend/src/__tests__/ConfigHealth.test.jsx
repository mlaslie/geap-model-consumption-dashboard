import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ConfigHealth from '../components/ConfigHealth.jsx';

function renderHealth(health, onNavigate = vi.fn()) {
  return render(<ConfigHealth health={health} onNavigate={onNavigate} />);
}

// ─── null health ─────────────────────────────────────────────────────────────

describe('ConfigHealth — null health', () => {
  it('renders nothing when health is null', () => {
    const { container } = renderHealth(null);
    expect(container).toBeEmptyDOMElement();
  });
});

// ─── Healthy / all-empty ─────────────────────────────────────────────────────

describe('ConfigHealth — all-empty lists', () => {
  const healthyHealth = {
    unused_budgets: [],
    logged_unused_models: [],
    used_unpriced_models: [],
    unlogged_used_models: [],
    usage_window_days: 30,
  };

  it('renders the single healthy confirmation line', () => {
    renderHealth(healthyHealth);
    expect(
      screen.getByText(/configuration healthy/i),
    ).toBeInTheDocument();
  });

  it('does not render any action links in the healthy state', () => {
    renderHealth(healthyHealth);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });
});

// ─── Populated lists ─────────────────────────────────────────────────────────

describe('ConfigHealth — populated list renders action link', () => {
  it('renders an "Add pricing →" action and calls onNavigate("planner") when clicked', async () => {
    const user = userEvent.setup();
    const onNavigate = vi.fn();
    renderHealth(
      {
        unused_budgets: [],
        logged_unused_models: [],
        used_unpriced_models: ['model-x'],
        unlogged_used_models: [],
        usage_window_days: 30,
      },
      onNavigate,
    );

    const btn = screen.getByRole('button', { name: /add pricing/i });
    expect(btn).toBeInTheDocument();
    await user.click(btn);
    expect(onNavigate).toHaveBeenCalledWith('planner');
  });

  it('renders an "Enable logging →" action and calls onNavigate("logging") when clicked', async () => {
    const user = userEvent.setup();
    const onNavigate = vi.fn();
    renderHealth(
      {
        unused_budgets: [],
        logged_unused_models: [],
        used_unpriced_models: [],
        unlogged_used_models: ['model-y'],
        usage_window_days: 30,
      },
      onNavigate,
    );

    const btn = screen.getByRole('button', { name: /enable logging/i });
    expect(btn).toBeInTheDocument();
    await user.click(btn);
    expect(onNavigate).toHaveBeenCalledWith('logging');
  });

  it('renders a "Review budgets →" action and calls onNavigate("budgets") when clicked', async () => {
    const user = userEvent.setup();
    const onNavigate = vi.fn();
    renderHealth(
      {
        unused_budgets: [{ identity: 'someone@example.com' }],
        logged_unused_models: [],
        used_unpriced_models: [],
        unlogged_used_models: [],
        usage_window_days: 30,
      },
      onNavigate,
    );

    const btn = screen.getByRole('button', { name: /review budgets/i });
    expect(btn).toBeInTheDocument();
    await user.click(btn);
    expect(onNavigate).toHaveBeenCalledWith('budgets');
  });
});

// ─── Truncation (>6 items) ────────────────────────────────────────────────────

describe('ConfigHealth — truncation', () => {
  it('shows "+N more" when a list exceeds 6 items', () => {
    // 8 items → 6 visible + "+2 more"
    const models = ['m1', 'm2', 'm3', 'm4', 'm5', 'm6', 'm7', 'm8'];
    renderHealth({
      unused_budgets: [],
      logged_unused_models: [],
      used_unpriced_models: models,
      unlogged_used_models: [],
      usage_window_days: 30,
    });

    expect(screen.getByText(/\+2 more/)).toBeInTheDocument();
    // m7 and m8 must NOT appear individually.
    expect(screen.queryByText(/m7/)).not.toBeInTheDocument();
    expect(screen.queryByText(/m8/)).not.toBeInTheDocument();
  });

  it('does NOT show "+N more" when the list has exactly 6 items', () => {
    const models = ['m1', 'm2', 'm3', 'm4', 'm5', 'm6'];
    renderHealth({
      unused_budgets: [],
      logged_unused_models: [],
      used_unpriced_models: models,
      unlogged_used_models: [],
      usage_window_days: 30,
    });

    expect(screen.queryByText(/more/)).not.toBeInTheDocument();
  });
});
