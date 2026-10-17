// Customer-facing wording for fact_consistency's per-field results
// (details.fields, built by backend/diagnose_service/engines/fact_engine.py).
// The backend also sends the individual runs, the votes and the voting rule;
// those stay in the API response for our own analysis and are never shown.
// Each field shows only its conclusion, what review sites say, and what the
// company's own site says.

export const VERDICT_TEXT = {
  match: 'Matches your site',
  conflict: "Doesn't match your site",
  unknown: 'Not enough information',
}

/** The conclusion for one field; anything unexpected reads as "not enough information". */
export function verdictText(verdict) {
  return VERDICT_TEXT[verdict] || VERDICT_TEXT.unknown
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
    thirdPartyValue: field.third_party_value || 'Not stated',
    thirdPartySource: field.third_party_source || null,
    officialValue: field.official_value || 'Not stated',
    officialQuote: field.official_quote || null,
  }))
}
