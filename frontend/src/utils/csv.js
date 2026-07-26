/**
 * CSV export utilities — RFC 4180 compliant.
 *
 * CSV-injection guard: string values starting with = + - @ are prefixed with a
 * single quote so spreadsheet applications (Excel, Google Sheets) do not
 * evaluate them as formulas. Numeric values are written raw and are never
 * guarded — genuine negative numbers must not be mangled.
 */

/**
 * Apply the CSV-injection guard to a string value.
 * @param {string} str
 * @returns {string}
 */
function guardInjection(str) {
  // Spreadsheets also evaluate formulas when the dangerous character is
  // preceded by whitespace, tab, or CR (e.g. "\t=CMD|..."), so test the first
  // NON-whitespace character rather than only position 0.
  return /^[\s]*[=+\-@]/.test(str) ? "'" + str : str;
}

/**
 * Serialize one field per RFC 4180:
 * - null / undefined → empty string
 * - Numbers written as-is (full precision, no formatting)
 * - Strings are injection-guarded, then wrapped in double-quotes when they
 *   contain a comma, double-quote, CR, or LF; internal double-quotes doubled.
 * @param {*} value
 * @returns {string}
 */
function serializeField(value) {
  if (value === null || value === undefined) return '';
  if (typeof value === 'number') {
    // Numbers go straight in — no injection guard, no quoting.
    return String(value);
  }
  const str = guardInjection(String(value));
  if (/[,"\r\n]/.test(str)) {
    return '"' + str.replace(/"/g, '""') + '"';
  }
  return str;
}

/**
 * Build an RFC 4180 CSV string from a column definition and an array of row objects.
 *
 * @param {{ key: string, header: string }[]} columns
 * @param {object[]} rows
 * @returns {string} CSV text (no BOM — added by downloadCsv)
 */
export function toCsv(columns, rows) {
  const header = columns.map(c => serializeField(c.header)).join(',');
  const body = rows.map(row =>
    columns.map(c => serializeField(row[c.key])).join(',')
  );
  return [header, ...body].join('\r\n');
}

/**
 * Trigger a client-side CSV download.
 * Prepends a UTF-8 BOM so Excel renders non-ASCII characters correctly.
 *
 * @param {string} filename
 * @param {string} csvString  Output of toCsv()
 */
export function downloadCsv(filename, csvString) {
  const BOM = '﻿';
  const blob = new Blob([BOM + csvString], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  // Revoke on a later tick: Firefox/Safari can cancel an in-flight download if
  // the object URL is revoked synchronously right after click().
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/**
 * Returns a local-time timestamp string in 'YYYYMMDD-HHmm' format,
 * suitable for embedding in export filenames.
 *
 * @returns {string}
 */
export function csvTimestamp() {
  const d = new Date();
  const Y = d.getFullYear();
  const M = String(d.getMonth() + 1).padStart(2, '0');
  const D = String(d.getDate()).padStart(2, '0');
  const h = String(d.getHours()).padStart(2, '0');
  const m = String(d.getMinutes()).padStart(2, '0');
  return `${Y}${M}${D}-${h}${m}`;
}
