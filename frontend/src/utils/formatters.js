/**
 * Utility functions for human-readable data representation in the FinOps dashboard.
 */

/**
 * Formats a numeric value as US currency.
 * Support high precision for fractional costs of individual model runs.
 * @param {number} value - The dollar amount to format.
 * @returns {string} Formatted currency string.
 */
export const formatCurrency = (value) => {
  if (value === undefined || value === null || isNaN(value)) return '$0.00';
  if (value === 0) return '$0.00';
  
  if (value < 0.01) {
    // High precision for sub-penny model queries (e.g., $0.00034)
    return `$${value.toFixed(5)}`;
  }
  
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  }).format(value);
};

/**
 * Formats a token integer count with commas.
 * @param {number} value - Number of tokens.
 * @returns {string} Formatted integer string.
 */
export const formatTokens = (value) => {
  if (value === undefined || value === null || isNaN(value)) return '0';
  return new Intl.NumberFormat('en-US').format(Math.round(value));
};

/**
 * Formats ISO timestamps into friendly readable times.
 * Format: "MMM dd, yyyy, h:mm a" (e.g., "Jul 20, 2026, 10:58 AM")
 * @param {string} isoString - ISO date string.
 * @returns {string} Friendly date representation.
 */
export const formatDateTime = (isoString) => {
  if (!isoString) return '—';
  try {
    const date = new Date(isoString);
    return new Intl.DateTimeFormat('en-US', {
      month: 'short',
      day: '2-digit',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      hour12: true
    }).format(date);
  } catch (e) {
    return isoString;
  }
};

/**
 * Shorthand label for model versions.
 * @param {string} modelName - Raw model name from API.
 * @returns {string} Sized-down badge label.
 */
export const getModelBadgeClass = (modelName) => {
  if (!modelName) return 'flash35';
  if (modelName.includes('pro')) return 'pro';
  if (modelName.includes('2.5')) return 'flash25';
  return 'flash35'; // Default to gemini-3.5-flash
};
