import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import UserTable from '../components/UserTable.jsx';

// Minimal props that satisfy the component without crashing.
const defaultBudgets = {};
const defaultBudgetStatus = null;

// Helpers
function makeLog(overrides = {}) {
  return {
    user_email: 'alice@example.com',
    model_name: 'gemini-1.5-flash',
    call_count: 1,
    input_tokens: 100,
    output_tokens: 50,
    total_tokens: 150,
    estimated_cost_usd: 0.01,
    pricing_match: 'exact',
    ...overrides,
  };
}

function renderTable(logs, extraProps = {}) {
  return render(
    <UserTable
      logs={logs}
      budgets={defaultBudgets}
      budgetStatus={defaultBudgetStatus}
      {...extraProps}
    />,
  );
}

// ─── Aggregation ──────────────────────────────────────────────────────────────

describe('UserTable — aggregation', () => {
  it('produces one row per (user, model) pair', () => {
    const logs = [
      makeLog({ user_email: 'alice@example.com', model_name: 'model-a' }),
      makeLog({ user_email: 'alice@example.com', model_name: 'model-b' }),
      makeLog({ user_email: 'bob@example.com',   model_name: 'model-a' }),
    ];
    renderTable(logs);
    // Three distinct (user, model) pairs → three data rows (excluding the header).
    const rows = screen.getAllByRole('row');
    // rows[0] is <thead> <tr>, rows[1..3] are body rows.
    expect(rows).toHaveLength(4); // 1 header + 3 body
  });

  it('sums call_count across multiple log entries for the same (user, model) pair', () => {
    const logs = [
      makeLog({ user_email: 'alice@example.com', model_name: 'model-a', call_count: 3 }),
      makeLog({ user_email: 'alice@example.com', model_name: 'model-a', call_count: 4 }),
    ];
    renderTable(logs);
    // The calls cell should display 7.
    expect(screen.getByText('7')).toBeInTheDocument();
  });
});

// ─── Unpriced rows ────────────────────────────────────────────────────────────

describe('UserTable — unpriced display', () => {
  it('shows "unpriced" (not $0.00) when all logs for a (user,model) pair have pricing_match "default"', () => {
    const logs = [
      makeLog({
        pricing_match: 'default',
        estimated_cost_usd: 0,
      }),
    ];
    renderTable(logs);
    expect(screen.getByText('unpriced')).toBeInTheDocument();
    // Must NOT show $0.00 for that row's cost cell.
    expect(screen.queryByText('$0.00')).not.toBeInTheDocument();
  });

  it('shows a formatted cost when at least one log is priced', () => {
    const logs = [
      makeLog({ pricing_match: 'default', estimated_cost_usd: 0 }),
      makeLog({ pricing_match: 'exact',   estimated_cost_usd: 0.05 }),
    ];
    renderTable(logs);
    // Mixed pricing: shows approximate (~) cost, not "unpriced".
    expect(screen.queryByText('unpriced')).not.toBeInTheDocument();
  });
});

// ─── Sorting ──────────────────────────────────────────────────────────────────

describe('UserTable — sorting', () => {
  const logs = [
    makeLog({ user_email: 'alice@example.com', model_name: 'model-a', estimated_cost_usd: 0.10 }),
    makeLog({ user_email: 'alice@example.com', model_name: 'model-b', estimated_cost_usd: 0.05 }),
    makeLog({ user_email: 'bob@example.com',   model_name: 'model-a', estimated_cost_usd: 0.20 }),
  ];

  it('clicking Cost header sorts descending on first click', async () => {
    const user = userEvent.setup();
    renderTable(logs);

    // The button's accessible name comes from its text content "Cost (USD)",
    // not from the title attribute "Sort by Cost (USD)".
    await user.click(screen.getByRole('button', { name: 'Cost (USD)' }));

    // After first click (defaultDir: 'desc') the header should indicate descending.
    const costTh = screen.getByRole('columnheader', { name: /cost \(usd\)/i });
    expect(costTh).toHaveAttribute('aria-sort', 'descending');
  });

  it('clicking Cost header twice toggles to ascending', async () => {
    const user = userEvent.setup();
    renderTable(logs);

    const btn = screen.getByRole('button', { name: 'Cost (USD)' });
    await user.click(btn);
    await user.click(btn);

    const costTh = screen.getByRole('columnheader', { name: /cost \(usd\)/i });
    expect(costTh).toHaveAttribute('aria-sort', 'ascending');
  });
});

// ─── Budget bar — one per principal regardless of sort ────────────────────────

describe('UserTable — budget bar', () => {
  it('renders exactly one progress bar per distinct principal, even when a non-grouped sort splits their rows', async () => {
    const user = userEvent.setup();
    // alice has two models with very different call counts so sorting by Calls
    // will insert bob's row between them.
    const logs = [
      makeLog({ user_email: 'alice@example.com', model_name: 'model-a', call_count: 10, total_tokens: 1000 }),
      makeLog({ user_email: 'alice@example.com', model_name: 'model-b', call_count: 2,  total_tokens: 200  }),
      makeLog({ user_email: 'bob@example.com',   model_name: 'model-a', call_count: 7,  total_tokens: 700  }),
    ];

    renderTable(logs);

    // Sort by Calls (desc by default) → order: alice/model-a(10), bob/model-a(7), alice/model-b(2)
    // Button accessible name is "Calls" (text content), not the title attribute.
    await user.click(screen.getByRole('button', { name: 'Calls' }));

    // The budget-progress-bar divs mark each budget display occurrence.
    const progressBars = document.querySelectorAll('.budget-progress-bar');
    // There are 2 distinct principals, so exactly 2 progress bars.
    expect(progressBars).toHaveLength(2);
  });
});

// ─── Filter ───────────────────────────────────────────────────────────────────

describe('UserTable — filter', () => {
  const logs = [
    makeLog({ user_email: 'alice@example.com', model_name: 'gemini-pro' }),
    makeLog({ user_email: 'bob@example.com',   model_name: 'gemini-flash' }),
  ];

  it('filters by principal email', async () => {
    const user = userEvent.setup();
    renderTable(logs);

    await user.type(
      screen.getByPlaceholderText(/filter by principal or model/i),
      'alice',
    );

    // Only alice's row should remain.
    const bodyRows = screen.getAllByRole('row').slice(1); // skip header
    expect(bodyRows).toHaveLength(1);
    expect(within(bodyRows[0]).getByText('alice@example.com')).toBeInTheDocument();
  });

  it('filters by model name', async () => {
    const user = userEvent.setup();
    renderTable(logs);

    await user.type(
      screen.getByPlaceholderText(/filter by principal or model/i),
      'flash',
    );

    // Only bob's row (gemini-flash) should remain.
    const bodyRows = screen.getAllByRole('row').slice(1);
    expect(bodyRows).toHaveLength(1);
    expect(within(bodyRows[0]).getByText('bob@example.com')).toBeInTheDocument();
  });
});
