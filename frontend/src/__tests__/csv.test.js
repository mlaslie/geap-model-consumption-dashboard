import { describe, it, expect, vi, afterEach } from 'vitest';
import { toCsv, downloadCsv, csvTimestamp } from '../utils/csv.js';

// ─── toCsv / serializeField ───────────────────────────────────────────────────

describe('toCsv — field serialization', () => {
  const col = (key) => [{ key, header: key }];
  const row = (key, value) => [{ [key]: value }];

  it('wraps a value containing a comma in double-quotes', () => {
    const csv = toCsv(col('v'), row('v', 'hello, world'));
    expect(csv).toContain('"hello, world"');
  });

  it('wraps a value containing a double-quote and doubles internal quotes', () => {
    const csv = toCsv(col('v'), row('v', 'say "hi"'));
    expect(csv).toContain('"say ""hi"""');
  });

  it('wraps a value containing a newline in double-quotes', () => {
    const csv = toCsv(col('v'), row('v', 'line1\nline2'));
    expect(csv).toContain('"line1\nline2"');
  });

  it('wraps a value containing a carriage-return in double-quotes', () => {
    const csv = toCsv(col('v'), row('v', 'line1\rline2'));
    expect(csv).toContain('"line1\rline2"');
  });

  it('leaves a plain value unquoted', () => {
    const csv = toCsv(col('v'), row('v', 'hello'));
    const [, dataRow] = csv.split('\r\n');
    expect(dataRow).toBe('hello');
  });

  it('renders null as an empty cell', () => {
    const csv = toCsv(col('v'), row('v', null));
    const [, dataRow] = csv.split('\r\n');
    expect(dataRow).toBe('');
  });

  it('renders undefined as an empty cell', () => {
    const csv = toCsv(col('v'), row('v', undefined));
    const [, dataRow] = csv.split('\r\n');
    expect(dataRow).toBe('');
  });
});

describe('toCsv — CSV-injection guard', () => {
  const col = (key) => [{ key, header: key }];
  const row = (key, value) => [{ [key]: value }];

  it('prefixes a string starting with = with a single quote', () => {
    const csv = toCsv(col('v'), row('v', '=SUM(A1)'));
    expect(csv).toContain("'=SUM(A1)");
  });

  it('prefixes a string starting with + with a single quote', () => {
    const csv = toCsv(col('v'), row('v', '+1234'));
    expect(csv).toContain("'+1234");
  });

  it('prefixes a string starting with - with a single quote', () => {
    const csv = toCsv(col('v'), row('v', '-CMD'));
    expect(csv).toContain("'-CMD");
  });

  it('prefixes a string starting with @ with a single quote', () => {
    const csv = toCsv(col('v'), row('v', '@foo'));
    expect(csv).toContain("'@foo");
  });

  it('does NOT mangle a genuine negative NUMBER (must stay raw)', () => {
    // Negative numbers are numeric type — they bypass string injection guard.
    const csv = toCsv(col('v'), row('v', -5));
    const [, dataRow] = csv.split('\r\n');
    expect(dataRow).toBe('-5');
  });
});

describe('toCsv — header row order', () => {
  it('matches the columns array order exactly', () => {
    const columns = [
      { key: 'z', header: 'ZZZ' },
      { key: 'a', header: 'AAA' },
      { key: 'm', header: 'MMM' },
    ];
    const rows = [{ z: 1, a: 2, m: 3 }];
    const [headerRow] = toCsv(columns, rows).split('\r\n');
    expect(headerRow).toBe('ZZZ,AAA,MMM');
  });
});

// ─── csvTimestamp ─────────────────────────────────────────────────────────────

describe('csvTimestamp', () => {
  it('matches the format YYYYMMDD-HHmm', () => {
    expect(csvTimestamp()).toMatch(/^\d{8}-\d{4}$/);
  });
});

// ─── downloadCsv ─────────────────────────────────────────────────────────────

describe('downloadCsv', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('creates a Blob, clicks the anchor, revokes the URL, and sets the filename', () => {
    const MOCK_URL = 'blob:http://localhost/fake-uuid';

    const createObjectURL = vi.fn(() => MOCK_URL);
    vi.useFakeTimers();
    const revokeObjectURL = vi.fn();

    // jsdom does not implement URL.createObjectURL; stub both methods.
    vi.stubGlobal('URL', { createObjectURL, revokeObjectURL });

    // Capture Blob constructor args so we can verify BOM presence.
    // NOTE: Blob.text() strips the U+FEFF BOM per the WHATWG TextDecoder spec,
    // so we must inspect the raw parts array instead of reading via .text().
    let capturedParts = null;
    const OrigBlob = globalThis.Blob;
    vi.stubGlobal(
      'Blob',
      class SpyBlob extends OrigBlob {
        constructor(parts, opts) {
          super(parts, opts);
          capturedParts = parts;
        }
      },
    );

    // Capture the anchor so we can verify the download attribute and that click fired.
    let capturedAnchor = null;
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(function () {
        capturedAnchor = this;
      });

    const csvContent = 'col_a,col_b\r\n1,2';
    downloadCsv('my-report.csv', csvContent);

    // A Blob was created and URL.createObjectURL was called.
    expect(createObjectURL).toHaveBeenCalledOnce();

    // The Blob parts must begin with the UTF-8 BOM (U+FEFF = 65279).
    expect(capturedParts).not.toBeNull();
    expect(capturedParts[0].charCodeAt(0)).toBe(0xfeff);
    expect(capturedParts[0].slice(1)).toBe(csvContent);

    // The anchor click must have fired.
    expect(clickSpy).toHaveBeenCalledOnce();

    // The filename is set via the download attribute.
    expect(capturedAnchor).not.toBeNull();
    expect(capturedAnchor.download).toBe('my-report.csv');

    // The object URL must be revoked after the click.
    // Revocation is deliberately deferred a tick: Firefox/Safari cancel an
    // in-flight download when the object URL is revoked synchronously after
    // click(). Advance timers to prove it still happens (no URL leak).
    expect(revokeObjectURL).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1000);
    expect(revokeObjectURL).toHaveBeenCalledWith(MOCK_URL);
  });
});

describe('csv injection guard hardening', () => {

  // Regression: both reviewers flagged that spreadsheets also evaluate a
  // formula when the dangerous character follows leading whitespace/tab/CR.
  it('guards formula characters preceded by whitespace, tab, or CR', () => {
    const rows = [
      { a: '\t=SUM(1,1)' },
      { a: ' =cmd|calc' },
      { a: '\r+1+1' },
      { a: '  @import' },
    ];
    const out = toCsv([{ key: 'a', header: 'a' }], rows);
    const lines = out.split('\n').slice(1);
    lines.forEach((line) => {
      // Every value must be neutralised with a leading apostrophe, whether or
      // not the field also got RFC-4180 quoted for containing CR/tab.
      expect(line.includes("'")).toBe(true);
    });
  });

  it('does not mangle a legitimate negative number', () => {
    const out = toCsv([{ key: 'n', header: 'n' }], [{ n: -5.25 }]);
    expect(out.split('\n')[1]).toBe('-5.25');
  });
});
