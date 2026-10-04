import { getScoreBand } from '../utils/scoreColor'
import { getDimensionLabel, getDimensionDescription } from '../utils/dimensionLabels'

// citation_visibility is the outcome (shown separately by
// CitationResultCard, never as a 0-100 score alongside these); this
// component only shows the 4 reason dimensions behind it.
const REASON_DIMENSIONS = ['topic_relevance', 'evidence_density', 'content_recency', 'fact_consistency']

export default function ScoreSummary({ dimensions }) {
  const reasons = dimensions.filter((dim) => REASON_DIMENSIONS.includes(dim.dimension))

  return (
    <div className="score-summary">
      {reasons.map((dim) => {
        const band = getScoreBand(dim.score)
        const hasScore = dim.score !== -1

        return (
          <div className="score-row" key={dim.dimension}>
            <div className="score-row-header">
              <span className="score-row-label">{getDimensionLabel(dim.dimension)}</span>
              <span className="score-row-value" style={{ color: band.color }}>
                {hasScore ? dim.score : 'Not enough data'}
              </span>
            </div>
            <div className="score-bar-track">
              {hasScore && (
                <div
                  className="score-bar-fill"
                  style={{ width: `${dim.score}%`, backgroundColor: band.color }}
                />
              )}
            </div>
            <p className="score-row-description">{getDimensionDescription(dim.dimension)}</p>
          </div>
        )
      })}
    </div>
  )
}
