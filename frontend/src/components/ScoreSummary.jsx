import { getScoreBand } from '../utils/scoreColor'
import { getDimensionLabel, getDimensionDescription } from '../utils/dimensionLabels'

export default function ScoreSummary({ dimensions }) {
  return (
    <div className="score-summary">
      {dimensions.map((dim) => {
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
