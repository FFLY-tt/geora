import { getScoreBand } from '../utils/scoreColor'
import { getDimensionLabel } from '../utils/dimensionLabels'

// A simple label + score list under the radar chart. Props: dimensions
// — the same 5 reason dimensions passed to RadarChart (citation_visibility
// excluded upstream in App.jsx).
export default function ScoreSummary({ dimensions }) {
  return (
    <ul className="score-list">
      {dimensions.map((dim) => {
        const band = getScoreBand(dim.score)
        const hasScore = dim.score !== -1

        return (
          <li className="score-list-row" key={dim.dimension}>
            <span className="score-list-label">{getDimensionLabel(dim.dimension)}</span>
            <span className="score-list-value" style={{ color: band.color }}>
              {hasScore ? dim.score : 'Not enough data'}
            </span>
          </li>
        )
      })}
    </ul>
  )
}
