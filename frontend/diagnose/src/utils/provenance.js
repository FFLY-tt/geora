// Helpers for a diagnosis result's `provenance` (see
// backend/diagnose_service/provenance.py): which measurement method
// version each dimension used, and which buyer question set version.
// Two results' scores are only comparable when both match exactly.

function sameMethodVersions(a, b) {
  const keysA = Object.keys(a || {})
  const keysB = Object.keys(b || {})
  if (keysA.length === 0 || keysA.length !== keysB.length) return false
  return keysA.every((key) => a[key] === b[key])
}

function sameQuestionSet(a, b) {
  if (!a && !b) return true
  if (!a || !b) return false
  return a.category_id === b.category_id && a.version === b.version
}

/**
 * True only when both results carry provenance and their method versions
 * and question set (category + version) are identical. A previous result
 * stored before provenance existed is never comparable.
 */
export function isComparable(previousProvenance, currentProvenance) {
  if (!previousProvenance || !currentProvenance) return false
  return (
    sameMethodVersions(previousProvenance.method_versions, currentProvenance.method_versions) &&
    sameQuestionSet(previousProvenance.question_set, currentProvenance.question_set)
  )
}

/** One short line describing the method and question set versions, for the result page footer. */
export function describeVersions(provenance) {
  if (!provenance) return ''
  const methods = Object.entries(provenance.method_versions || {})
    .map(([dimension, version]) => `${dimension} ${version}`)
    .join(', ')
  const questionSet = provenance.question_set
  const questionSetText = questionSet
    ? `${questionSet.category_id} v${questionSet.version}${questionSet.status === 'needs_attention' ? ' (quality pending review)' : ''}`
    : 'none'
  return `Method versions: ${methods}. Question set: ${questionSetText}. Run date: ${provenance.run_date}.`
}
