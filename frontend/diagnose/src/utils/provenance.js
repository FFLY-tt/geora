// Helpers for a diagnosis result's `provenance` (see
// backend/diagnose_service/provenance.py): which measurement method
// version each dimension used, and which buyer question set version.
// Two results' scores are only comparable when both match exactly.
//
// Method and question-set versions are internal details. They are never
// shown on the page; they only decide, silently, whether a "your scores
// moved" note may appear at all. The full provenance stays in the API
// response for our own analysis.

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

// A dimension score moved by more than this many points to count as a
// "real" change worth mentioning, versus normal day-to-day noise.
const CHANGE_THRESHOLD = 5

// -1 means "insufficient data" on either side, not a real score — never
// treat a swing into or out of -1 as a "score change".
function isRealChange(previousScore, currentScore) {
  if (previousScore === undefined || previousScore === null) return false
  if (previousScore === -1 || currentScore === -1) return false
  return Math.abs(currentScore - previousScore) > CHANGE_THRESHOLD
}

/**
 * Whether the "your scores moved" note should appear. False on a
 * first-ever check, and false (with no other message) when the two
 * results were measured with different method or question-set versions.
 */
export function shouldShowScoreChange(previousScores, currentScores, previousProvenance, currentProvenance) {
  if (!previousScores) return false
  if (!isComparable(previousProvenance, currentProvenance)) return false
  return currentScores.some((dim) => isRealChange(previousScores[dim.dimension], dim.score))
}
