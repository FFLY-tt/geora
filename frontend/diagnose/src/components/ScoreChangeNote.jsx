import { isComparable } from '../utils/provenance.js'
import { describeChange } from '../utils/mentionInterval.js'

// Props: previousScores ({ [dimension]: score } from the backend, or
// null on a first-ever check), currentScores (the current `dimensions`
// array, each with .dimension and .score), previousProvenance and
// currentProvenance (method and question set versions of each result),
// previousCitationInterval and currentCitationInterval ({ci_low, ci_high}
// Wilson intervals of the citation mention rate).
// Scores are only compared when both versions match; otherwise the
// numbers measure different things and are not compared at all. A
// citation change whose intervals overlap is within the margin of error
// and is never described as a rise or a drop.
export default function ScoreChangeNote({
  previousScores,
  currentScores,
  previousProvenance,
  currentProvenance,
  previousCitationInterval,
  currentCitationInterval,
}) {
  if (!previousScores) {
    return null
  }

  if (!isComparable(previousProvenance, currentProvenance)) {
    return (
      <p className="score-change-note">
        The measurement method has been updated, so this result can't be directly compared with your last one.
      </p>
    )
  }

  const { realChange, withinMargin } = describeChange(
    previousScores,
    currentScores,
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
      {withinMargin && (
        <p className="score-change-note">
          Your AI mention rate changed since last time, but the change is within the margin of error, so it
          isn't a real rise or drop.
        </p>
      )}
    </>
  )
}
