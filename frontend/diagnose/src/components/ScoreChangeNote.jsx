// A dimension score moved by more than this many points to count as a
// "real" change worth mentioning, versus normal day-to-day noise.
const CHANGE_THRESHOLD = 5

// -1 means "insufficient data" on either side, not a real score — never
// treat a swing into or out of -1 as a "score change" to report here.
function isRealChange(previousScore, currentScore) {
  if (previousScore === undefined || previousScore === null) return false
  if (previousScore === -1 || currentScore === -1) return false
  return Math.abs(currentScore - previousScore) > CHANGE_THRESHOLD
}

// Props: previousScores ({ [dimension]: score } from the backend, or
// null on a first-ever check), currentScores (the current `dimensions`
// array, each with .dimension and .score).
export default function ScoreChangeNote({ previousScores, currentScores }) {
  if (!previousScores) {
    return null
  }

  const hasRealChange = currentScores.some((dim) => isRealChange(previousScores[dim.dimension], dim.score))

  if (!hasRealChange) {
    return null
  }

  return (
    <p className="score-change-note">
      Your scores moved a bit since last time. Small day-to-day changes are normal because AI search
      results update constantly. Focus on the trend over several weeks, not any single check.
    </p>
  )
}
