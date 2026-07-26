import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import BudgetConsumption from '../components/BudgetConsumption.jsx';

// Helper: build a budgetStatus entry.
function makeEntry(overrides = {}) {
  return {
    percentage: 30,
    threshold_percentage: 50,
    type: 'token',
    period: 'month',
    limit: 100000,
    consumed: 30000,
    hard_limit_enabled: false,
    is_global_default: false,
    ...overrides,
  };
}

function renderBC(budgetStatus, extraProps = {}) {
  return render(
    <BudgetConsumption
      budgetStatus={budgetStatus}
      budgets={{}}
      onNavigateToBudgets={vi.fn()}
      {...extraProps}
    />,
  );
}

// ─── Empty state ──────────────────────────────────────────────────────────────

describe('BudgetConsumption — empty state', () => {
  it('renders the "Configure budgets" action button when budgetStatus is empty', () => {
    renderBC({});
    expect(screen.getByRole('button', { name: /configure budgets/i })).toBeInTheDocument();
  });

  it('calls onNavigateToBudgets when the "Configure budgets" button is clicked', async () => {
    const onNavigateToBudgets = vi.fn();
    const user = userEvent.setup();
    render(
      <BudgetConsumption
        budgetStatus={{}}
        budgets={{}}
        onNavigateToBudgets={onNavigateToBudgets}
      />,
    );
    await user.click(screen.getByRole('button', { name: /configure budgets/i }));
    expect(onNavigateToBudgets).toHaveBeenCalledOnce();
  });

  it('renders the empty state when budgetStatus is null', () => {
    renderBC(null);
    expect(screen.getByRole('button', { name: /configure budgets/i })).toBeInTheDocument();
  });
});

// ─── Status pill classification ───────────────────────────────────────────────

describe('BudgetConsumption — status pill classification', () => {
  it('shows OK pill when percentage is below threshold', () => {
    renderBC({ 'user@example.com': makeEntry({ percentage: 20, threshold_percentage: 50 }) });
    expect(screen.getByText('OK')).toBeInTheDocument();
  });

  it('shows APPROACHING pill when percentage is at or above threshold but below 100', () => {
    renderBC({ 'user@example.com': makeEntry({ percentage: 60, threshold_percentage: 50 }) });
    expect(screen.getByText('APPROACHING')).toBeInTheDocument();
  });

  it('shows OVER LIMIT pill when percentage is 100 or above', () => {
    renderBC({ 'user@example.com': makeEntry({ percentage: 105, threshold_percentage: 50 }) });
    expect(screen.getByText('OVER LIMIT')).toBeInTheDocument();
  });

  it('shows APPROACHING at exactly threshold', () => {
    renderBC({ 'user@example.com': makeEntry({ percentage: 50, threshold_percentage: 50 }) });
    expect(screen.getByText('APPROACHING')).toBeInTheDocument();
  });

  it('shows OVER LIMIT at exactly 100%', () => {
    renderBC({ 'user@example.com': makeEntry({ percentage: 100, threshold_percentage: 50 }) });
    expect(screen.getByText('OVER LIMIT')).toBeInTheDocument();
  });
});

// ─── Status column sorts by severity, not alphabetically ─────────────────────

describe('BudgetConsumption — Status column sort order', () => {
  it('sorts over-limit rows FIRST on the first click (desc by default)', async () => {
    const user = userEvent.setup();
    const budgetStatus = {
      'alice@example.com': makeEntry({ percentage: 20,  threshold_percentage: 50 }), // OK
      'bob@example.com':   makeEntry({ percentage: 60,  threshold_percentage: 50 }), // APPROACHING
      'carol@example.com': makeEntry({ percentage: 110, threshold_percentage: 50 }), // OVER LIMIT
    };

    renderBC(budgetStatus);

    // Button accessible name is "Status" (text content), not the title attribute.
    await user.click(screen.getByRole('button', { name: 'Status' }));

    // After first click (defaultDir: 'desc'), highest severity first.
    // The first data row should display the OVER LIMIT principal (carol).
    const bodyRows = screen.getAllByRole('row').slice(1); // skip header
    expect(within(bodyRows[0]).getByText('carol@example.com')).toBeInTheDocument();
  });

  it('sorts within-budget rows LAST on the first Status click', async () => {
    const user = userEvent.setup();
    const budgetStatus = {
      'alice@example.com': makeEntry({ percentage: 20,  threshold_percentage: 50 }),
      'bob@example.com':   makeEntry({ percentage: 110, threshold_percentage: 50 }),
    };

    renderBC(budgetStatus);

    await user.click(screen.getByRole('button', { name: 'Status' }));

    const bodyRows = screen.getAllByRole('row').slice(1);
    // Last row should be alice (OK, lowest severity).
    expect(within(bodyRows[bodyRows.length - 1]).getByText('alice@example.com')).toBeInTheDocument();
  });
});
