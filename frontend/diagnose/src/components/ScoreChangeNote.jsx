import { scoreChangeNotes } from '../utils/provenance.js'

// Props: previousScores ({ [dimension]: score } from the backend, or
// null on a first-ever check), currentScores (the current `dimensions`
// array, each with .dimension and .score), previousProvenance and
// currentProvenance (method and question set versions of each result),
// previousCitationInterval and currentCitationInterval ({ci_low, ci_high}
// Wilson intervals of the citation mention rate).
// When the versions differ the scores measure different things, so
// nothing is shown at all: versions are internal details, never page text.
// A citation change whose intervals overlap is never described as a rise
// or a drop; the customer is told it is ordinary day-to-day variation.
export default function ScoreChangeNote({
  previousScores,
  currentScores,
  previousProvenance,
  currentProvenance,
  previousCitationInterval,
  currentCitationInterval,
}) {
  const { realChange, withinMargin } = scoreChangeNotes(
    previousScores,
    currentScores,
    previousProvenance,
    currentProvenance,
    previousCitationInterval,
    currentCitationInterval,
  )

  return (
    <>
      {realChange && (
        <p className="score-change-note">
          Your scores moved a bit since last time. Small day-to-day changes are normal because AI search
          results update constantly. Focus on the trend over several weeks, not any single check.
        </p>
      )}
      {withinMargin && <p className="score-change-note">This change is within normal day-to-day variation.</p>}
    </>
  )
}
