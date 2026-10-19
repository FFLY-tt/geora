import { readinessRows, SITE_READINESS_TITLE, totalText } from '../utils/siteReadiness.js'

// "Is your website ready for AI?": the total on the right of the title and one row per check that
// has a result, with four things on it: the name, a bar as long as the score, the score and the level
// as a word. The bar is blue up to the score and light gray after it. Props: readiness
// (result.site_readiness). Nothing is drawn when no check has a result.
export default function SiteReadiness({ readiness }) {
  const rows = readinessRows(readiness)
  const total = totalText(readiness)
  if (rows.length === 0) return null

  return (
    <section className="bars-block">
      <div className="bars-heading">
        <h3 className="bars-title">{SITE_READINESS_TITLE}</h3>
        {total && <p className="bars-total">{total}</p>}
      </div>
      <div className="bars">
        {rows.map((row) => (
          <div key={row.key} className="bar-row">
            <span className="bar-name">{row.name}</span>
            <span className="bar-track bar-track-full">
              <span className="bar-part bar-part-blue" style={{ width: `${row.fill}%` }} />
            </span>
            <span className="bar-number">{row.score}</span>
            <span className="bar-level">{row.level}</span>
          </div>
        ))}
      </div>
    </section>
  )
}
