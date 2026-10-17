// Helpers for a diagnosis result's `provenance` (see
// backend/diagnose_service/provenance.py): which measurement method
// version each dimension used, and which buyer question set version.
// Two results' scores are only comparable when both match exactly.
//
// Method and question-set versions are internal details. They are never
// shown on the page; they only decide, silently, whether a "your scores
// moved" note may appear at all. The full provenance stays in the API
// response for our own analysis.

import { describeChange } from './mentionInterval.js'

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

/**
 * What the "your scores moved" notes should say. Nothing at all on a
 * first-ever check, and nothing (no other message either) when the two
 * results were measured with different method or question-set versions.
 * Otherwise see describeChange: { realChange, withinMargin }.
 */
export function scoreChangeNotes(
  previousScores,
  currentScores,
  previousProvenance,
  currentProvenance,
  previousInterval = null,
  currentInterval = null,
) {
  if (!previousScores || !isComparable(previousProvenance, currentProvenance)) {
    return { realChange: false, withinMargin: false }
  }
  return describeChange(previousScores, currentScores, previousInterval, currentInterval)
}

/** Whether any "your scores moved" note should appear. */
export function shouldShowScoreChange(previousScores, currentScores, previousProvenance, currentProvenance) {
  const { realChange, withinMargin } = scoreChangeNotes(previousScores, currentScores, previousProvenance, currentProvenance)
  return realChange || withinMargin
}
