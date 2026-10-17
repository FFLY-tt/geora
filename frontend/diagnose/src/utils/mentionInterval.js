// Mention-rate helpers for citation_visibility. The backend sends
// mentions, total, ci_low, ci_high (Wilson 95% interval) in the
// dimension's details; with only ~15 answers a single percentage hides
// how uncertain it is, so we always show the likely range too.

function pct(value) {
  return `${Math.round(value * 100)}%`
}

/** "Mentioned in 3 of 15 answers (20%). Likely range: 7%–45%." or null when there is nothing to show. */
export function mentionSummary(details) {
  if (!details || !details.total) return null
  const { mentions, total, ci_low: low, ci_high: high } = details
  const base = `Mentioned in ${mentions} of ${total} answers (${pct(mentions / total)}).`
  if (low === null || low === undefined || high === null || high === undefined) return base
  return `${base} Likely range: ${pct(low)}–${pct(high)}.`
}

/** True when two {ci_low, ci_high} intervals overlap (touching counts as overlapping). */
export function intervalsOverlap(a, b) {
  if (!a || !b) return false
  if ([a.ci_low, a.ci_high, b.ci_low, b.ci_high].some((v) => v === null || v === undefined)) return false
  return a.ci_low <= b.ci_high && b.ci_low <= a.ci_high
}

// A dimension score moved by more than this many points to count as a
// "real" change worth mentioning, versus normal day-to-day noise.
const CHANGE_THRESHOLD = 5

function isRealChange(previousScore, currentScore) {
  if (previousScore === undefined || previousScore === null) return false
  if (previousScore === -1 || currentScore === -1) return false
  return Math.abs(currentScore - previousScore) > CHANGE_THRESHOLD
}

/**
 * How to describe the change since the last comparable check.
 * citation_visibility is judged by its interval when both results have
 * one: overlapping intervals mean the change is within the margin of
 * error, never a rise or a drop. Every other dimension keeps the
 * CHANGE_THRESHOLD rule.
 * Returns { realChange, withinMargin }.
 */
export function describeChange(previousScores, currentDimensions, previousInterval, currentInterval) {
  let realChange = false
  let withinMargin = false
  for (const dim of currentDimensions) {
    const previous = previousScores?.[dim.dimension]
    if (dim.dimension === 'citation_visibility' && previousInterval && currentInterval) {
      if (previous === undefined || previous === null || previous === -1 || dim.score === -1) continue
      if (intervalsOverlap(previousInterval, currentInterval)) {
        if (previous !== dim.score) withinMargin = true
      } else {
        realChange = true
      }
      continue
    }
    if (isRealChange(previous, dim.score)) realChange = true
  }
  return { realChange, withinMargin }
}
