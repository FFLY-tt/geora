import { comparisonLines } from '../utils/competitors.js'

// Simple list under the mention rate: you and each competitor the visitor
// typed in, how many of the answers mention them, most first. Shown only when
// competitors were entered (the backend sends no comparison otherwise).
export default function CompetitorList({ comparison }) {
  const lines = comparisonLines(comparison)
  if (lines.length === 0) return null

  return (
    <div className="competitor-list">
      <p className="competitor-list-title">How often each company is mentioned</p>
      <ul>
        {lines.map((line) => (
          <li key={line.key} className={line.isYou ? 'competitor-you' : ''}>
            <span className="competitor-name">{line.isYou ? `${line.name} (you)` : line.name}</span>
            <span className="competitor-count">{line.text}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
