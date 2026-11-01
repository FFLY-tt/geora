// Customer-facing wording for fact_consistency's per-field results
// (details.fields, built by backend/diagnose_service/engines/fact_engine.py).
// The backend also sends the individual runs, the votes and the voting rule;
// those stay in the API response for our own analysis and are never shown.
// Each field shows only its conclusion, what review sites say, and what the
// company's own site says.

import { plainText } from './text.js'

export const VERDICT_TEXT = {
  match: 'Matches your site',
  conflict: "Doesn't match your site",
  unknown: 'Not enough information',
}

/** The conclusion for one field; anything unexpected reads as "not enough information". */
export function verdictText(verdict) {
  return VERDICT_TEXT[verdict] || VERDICT_TEXT.unknown
}

// The most characters of the official text shown for one field (before the "…" marks).
export const SNIPPET_LENGTH = 120

/**
 * A short piece of the official text around `value`, for display only (the
 * backend has already checked that the whole quote appears word for word in the
 * page text). Whitespace is collapsed; a quote that fits is shown whole;
 * otherwise a window of about SNIPPET_LENGTH characters that contains the value
 * is cut at word boundaries, with "…" at each end that was cut off.
 */
export function quoteSnippet(quote, value, max = SNIPPET_LENGTH) {
  const text = String(quote || '').replace(/\s+/g, ' ').trim()
  if (text.length <= max) return text

  const target = String(value || '').replace(/\s+/g, ' ').trim()
  const at = target ? text.toLowerCase().indexOf(target.toLowerCase()) : -1
  const valueStart = at === -1 ? 0 : at
  const valueEnd = at === -1 ? 0 : Math.min(text.length, at + target.length)

  // Center the value in the window; a value longer than the window starts the window.
  let start = Math.max(0, Math.min(valueStart - Math.floor((max - (valueEnd - valueStart)) / 2), text.length - max))
  if (valueEnd - valueStart >= max) start = valueStart
  let end = Math.min(text.length, start + max)

  // Cut at word boundaries, but never inside the value.
  while (start > 0 && start < valueStart && text[start - 1] !== ' ') start += 1
  while (end < text.length && end > valueEnd && text[end] !== ' ') end -= 1

  const body = text.slice(start, end).trim()
  return `${start > 0 ? '…' : ''}${body}${end < text.length ? '…' : ''}`
}

/**
 * Rows for the field list: label, conclusion, the review sites' value with its
 * source link, and your site's value with the exact quote. Fields without a
 * value say so instead of showing an empty line.
 */
export function factRows(details) {
  const fields = details?.fields
  if (!Array.isArray(fields)) return []
  return fields.map((field) => ({
    key: field.field,
    label: field.label || field.field,
    verdictText: verdictText(field.verdict),
    thirdPartyValue: plainText(field.third_party_value) || 'Not stated',
    thirdPartySource: field.third_party_source || null,
    officialValue: plainText(field.official_value) || 'Not stated',
    officialQuote: field.official_quote ? plainText(quoteSnippet(field.official_quote, field.official_value)) : null,
  }))
}
