import { shouldShowScoreChange } from '../utils/provenance.js'

// Props: previousScores ({ [dimension]: score } from the backend, or
// null on a first-ever check), currentScores (the current `dimensions`
// array, each with .dimension and .score), previousProvenance and
// currentProvenance (method and question set versions of each result).
// When the versions differ the scores measure different things, so
// nothing is shown at all: versions are internal details, never page text.
export default function ScoreChangeNote({ previousScores, currentScores, previousProvenance, currentProvenance }) {
  if (!shouldShowScoreChange(previousScores, currentScores, previousProvenance, currentProvenance)) {
    return null
  }

  return (
    <p className="score-change-note">
      Your scores moved a bit since last time. Small day-to-day changes are normal because AI search
      results update constantly. Focus on the trend over several weeks, not any single check.
    </p>
  )
}
