import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

// vi.mock is hoisted above imports by Vitest's transformer.
vi.mock('../utils/api', () => ({
  apiFetch: vi.fn(),
}));

vi.mock('../utils/csv', () => ({
  toCsv: vi.fn(() => 'col1,col2\r\nval1,val2'),
  downloadCsv: vi.fn(),
  csvTimestamp: vi.fn(() => '20260725-1200'),
}));

import MonthlyStatement from '../components/MonthlyStatement.jsx';
import { apiFetch } from '../utils/api';
import { downloadCsv } from '../utils/csv';

// ─── Fixture helpers ──────────────────────────────────────────────────────────

const MOCK_MONTH = '2026-07';

function makeData(overrides = {}) {
  return {
    month: MOCK_MONTH,
    period_start: '2026-07-01',
    period_end_exclusive: '2026-08-01',
    generated_at_utc: '2026-07-25T12:00:00Z',
    totals: {
      cost_usd: 12.34,
      input_tokens: 50000,
      output_tokens: 20000,
      thoughts_tokens: 5000,
      total_tokens: 70000,
      calls: 100,
      principals: 2,
    },
    per_principal: [
      {
        user_email: 'alice@example.com',
        cost_usd: 7.50,
        input_tokens: 30000,
        output_tokens: 12000,
        thoughts_tokens: 3000,
        total_tokens: 42000,
        calls: 60,
        models: ['gemini-flash', 'gemini-pro'],
      },
      {
        user_email: 'bob@example.com',
        cost_usd: 4.84,
        input_tokens: 20000,
        output_tokens: 8000,
        thoughts_tokens: 2000,
        total_tokens: 28000,
        calls: 40,
        models: ['gemini-flash'],
      },
    ],
    per_model: [
      {
        model_name: 'gemini-flash',
        cost_usd: 10.00,
        input_tokens: 40000,
        output_tokens: 16000,
        thoughts_tokens: 4000,
        total_tokens: 56000,
        calls: 80,
      },
      {
        model_name: 'gemini-pro',
        cost_usd: 2.34,
        input_tokens: 10000,
        output_tokens: 4000,
        thoughts_tokens: 1000,
        total_tokens: 14000,
        calls: 20,
      },
    ],
    pricing_assumptions: {
      rates_as_of_utc: '2026-07-01T00:00:00Z',
      note: 'Rates from official pricing page.',
      models: {},
    },
    unpriced_models: [],
    budget_snapshot: [],
    ...overrides,
  };
}

function mockSuccess(data) {
  apiFetch.mockResolvedValueOnce({
    ok: true,
    json: () => Promise.resolve({ status: 'success', data }),
  });
}

function mock400(detail) {
  apiFetch.mockResolvedValueOnce({
    ok: false,
    status: 400,
    json: () => Promise.resolve({ detail }),
  });
}

beforeEach(() => {
  vi.clearAllMocks();
});

// ─── Successful load ──────────────────────────────────────────────────────────

describe('MonthlyStatement — successful load', () => {
  it('renders totals after a successful fetch', async () => {
    mockSuccess(makeData());
    render(<MonthlyStatement />);

    // Wait for the statement header to appear (async load).
    await screen.findByText(/Statement — July 2026/i);

    // Totals strip values — formatCurrency(12.34) → "$12.34"
    expect(screen.getByText('$12.34')).toBeInTheDocument();
    // "100" appears in the Calls tile; use getAllByText and verify at least one
    expect(screen.getAllByText('100').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('70,000')).toBeInTheDocument(); // total tokens
  });

  it('renders the per-principal table rows', async () => {
    mockSuccess(makeData());
    render(<MonthlyStatement />);

    await screen.findByText('alice@example.com');
    expect(screen.getByText('bob@example.com')).toBeInTheDocument();
  });

  it('renders the per-model table rows with unique cost values', async () => {
    mockSuccess(makeData());
    render(<MonthlyStatement />);

    // Wait for the By Model heading to confirm that section rendered.
    await screen.findByText('By Model');
    // $10.00 and $2.34 are unique to the per-model rows.
    expect(screen.getByText('$10.00')).toBeInTheDocument();
    expect(screen.getByText('$2.34')).toBeInTheDocument();
  });

  it('shows reasoning tokens sub-note when thoughts_tokens > 0', async () => {
    mockSuccess(makeData());
    render(<MonthlyStatement />);

    await screen.findByText(/incl\. 5,000 reasoning/i);
  });
});

// ─── 400 error ───────────────────────────────────────────────────────────────

describe('MonthlyStatement — 400 error', () => {
  it('shows the backend detail message on a 400 response', async () => {
    mock400('Month 2026-13 is out of range or in the future.');
    render(<MonthlyStatement />);

    await screen.findByText('Month 2026-13 is out of range or in the future.');
  });

  it('does not render statement content when an error occurred', async () => {
    mock400('Invalid month format.');
    render(<MonthlyStatement />);

    await screen.findByText('Invalid month format.');
    expect(screen.queryByText(/Statement —/)).not.toBeInTheDocument();
  });
});

// ─── Download CSV ─────────────────────────────────────────────────────────────

describe('MonthlyStatement — download CSV', () => {
  it('calls downloadCsv with a filename containing the statement month', async () => {
    const user = userEvent.setup();
    mockSuccess(makeData());
    render(<MonthlyStatement />);

    await screen.findByText('alice@example.com');

    await user.click(screen.getByRole('button', { name: /download statement csv/i }));

    expect(downloadCsv).toHaveBeenCalledOnce();
    const [filename] = downloadCsv.mock.calls[0];
    expect(filename).toContain(MOCK_MONTH);
  });

  it('includes the month in the CSV filename as statement-<month>-<timestamp>.csv', async () => {
    const user = userEvent.setup();
    mockSuccess(makeData());
    render(<MonthlyStatement />);

    await screen.findByText('alice@example.com');

    await user.click(screen.getByRole('button', { name: /download statement csv/i }));

    const [filename] = downloadCsv.mock.calls[0];
    expect(filename).toMatch(/^statement-2026-07-\d{8}-\d{4}\.csv$/);
  });
});

// ─── Unpriced-models warning ──────────────────────────────────────────────────

describe('MonthlyStatement — unpriced models warning', () => {
  it('does NOT show the warning when unpriced_models is empty', async () => {
    mockSuccess(makeData({ unpriced_models: [] }));
    render(<MonthlyStatement />);

    await screen.findByText(/Statement — July 2026/i);
    expect(screen.queryByText(/no configured pricing/i)).not.toBeInTheDocument();
  });

  it('shows the warning when unpriced_models is non-empty', async () => {
    mockSuccess(makeData({ unpriced_models: ['mystery-model-v1', 'mystery-model-v2'] }));
    render(<MonthlyStatement />);

    await screen.findByText(/2 model\(s\) in this period have no configured pricing/i);
  });

  it('shows singular count in the warning for one unpriced model', async () => {
    mockSuccess(makeData({ unpriced_models: ['mystery-model-v1'] }));
    render(<MonthlyStatement />);

    await screen.findByText(/1 model\(s\) in this period have no configured pricing/i);
  });
});

// ─── Empty / zero-totals ──────────────────────────────────────────────────────

describe('MonthlyStatement — zero totals', () => {
  it('renders without crashing when all totals are zero and arrays are empty', async () => {
    mockSuccess(makeData({
      totals: {
        cost_usd: 0,
        input_tokens: 0,
        output_tokens: 0,
        thoughts_tokens: 0,
        total_tokens: 0,
        calls: 0,
        principals: 0,
      },
      per_principal: [],
      per_model: [],
      unpriced_models: [],
    }));

    expect(() => render(<MonthlyStatement />)).not.toThrow();

    // Statement header still appears
    await screen.findByText(/Statement — July 2026/i);
  });

  it('does not show the reasoning sub-note when thoughts_tokens is 0', async () => {
    mockSuccess(makeData({
      totals: {
        cost_usd: 0,
        input_tokens: 0,
        output_tokens: 0,
        thoughts_tokens: 0,
        total_tokens: 0,
        calls: 0,
        principals: 0,
      },
      per_principal: [],
      per_model: [],
    }));

    render(<MonthlyStatement />);
    await screen.findByText(/Statement — July 2026/i);

    expect(screen.queryByText(/reasoning/i)).not.toBeInTheDocument();
  });
});
