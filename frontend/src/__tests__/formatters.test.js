import { describe, it, expect } from 'vitest';
import {
  formatCurrency,
  formatTokens,
  formatDateTime,
  getModelBadgeClass,
} from '../utils/formatters.js';

// ─── formatCurrency ───────────────────────────────────────────────────────────

describe('formatCurrency', () => {
  it('returns $0.00 for null', () => {
    expect(formatCurrency(null)).toBe('$0.00');
  });

  it('returns $0.00 for undefined', () => {
    expect(formatCurrency(undefined)).toBe('$0.00');
  });

  it('returns $0.00 for NaN', () => {
    expect(formatCurrency(NaN)).toBe('$0.00');
  });

  it('returns $0.00 for zero', () => {
    expect(formatCurrency(0)).toBe('$0.00');
  });

  it('uses 5 decimal places for sub-penny values', () => {
    // Values below $0.01 get .toFixed(5)
    expect(formatCurrency(0.00034)).toBe('$0.00034');
    expect(formatCurrency(0.000005)).toBe('$0.00001');
  });

  it('formats normal values with Intl currency formatting', () => {
    const result = formatCurrency(1.5);
    expect(result).toMatch(/^\$1\.50/);
  });

  it('formats values with up to 4 decimal places for high-precision costs', () => {
    // 0.1234 is >= 0.01 so goes through Intl — should keep at most 4 decimal places
    const result = formatCurrency(0.1234);
    expect(result).toMatch(/^\$0\.1234/);
  });
});

// ─── formatTokens ─────────────────────────────────────────────────────────────

describe('formatTokens', () => {
  it('returns "0" for null', () => {
    expect(formatTokens(null)).toBe('0');
  });

  it('returns "0" for undefined', () => {
    expect(formatTokens(undefined)).toBe('0');
  });

  it('returns "0" for NaN', () => {
    expect(formatTokens(NaN)).toBe('0');
  });

  it('rounds fractional values', () => {
    expect(formatTokens(1.7)).toBe('2');
    expect(formatTokens(1.2)).toBe('1');
  });

  it('adds thousands separators for large values', () => {
    expect(formatTokens(1000000)).toBe('1,000,000');
    expect(formatTokens(12345)).toBe('12,345');
  });

  it('handles zero', () => {
    expect(formatTokens(0)).toBe('0');
  });
});

// ─── formatDateTime ───────────────────────────────────────────────────────────

describe('formatDateTime', () => {
  it('returns an em dash for null', () => {
    expect(formatDateTime(null)).toBe('—');
  });

  it('returns an em dash for undefined', () => {
    expect(formatDateTime(undefined)).toBe('—');
  });

  it('returns an em dash for empty string', () => {
    expect(formatDateTime('')).toBe('—');
  });

  it('formats a valid ISO string into a human-readable date', () => {
    // Exact output depends on locale/timezone but should include year and time.
    const result = formatDateTime('2026-07-20T10:58:00.000Z');
    expect(result).toMatch(/2026/);
    // Should contain AM or PM (12-hour format).
    expect(result).toMatch(/[AP]M/);
  });
});

// ─── getModelBadgeClass ───────────────────────────────────────────────────────

describe('getModelBadgeClass', () => {
  it('returns "flash35" for null/undefined (default)', () => {
    expect(getModelBadgeClass(null)).toBe('flash35');
    expect(getModelBadgeClass(undefined)).toBe('flash35');
  });

  it('returns "pro" for model names containing "pro"', () => {
    expect(getModelBadgeClass('gemini-1.5-pro')).toBe('pro');
    expect(getModelBadgeClass('some-pro-model')).toBe('pro');
  });

  it('returns "flash25" for model names containing "2.5"', () => {
    expect(getModelBadgeClass('gemini-2.5-flash')).toBe('flash25');
  });

  it('returns "flash35" for unrecognised model names', () => {
    expect(getModelBadgeClass('gemini-1.5-flash')).toBe('flash35');
    expect(getModelBadgeClass('unknown-model')).toBe('flash35');
  });

  it('prioritises "pro" over "2.5" when both appear in the name', () => {
    // "pro" check comes first in the source
    expect(getModelBadgeClass('gemini-2.5-pro')).toBe('pro');
  });
});
