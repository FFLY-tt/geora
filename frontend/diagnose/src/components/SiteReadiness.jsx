import { readinessRows, SITE_READINESS_TITLE, totalText } from '../utils/siteReadiness.js'

// "Is your website ready for AI?": the total, one bar per check of the company's own website, and
// one closing sentence. The filled part of a bar is blue and the rest is light gray. Each row says
// its level in words. Props: readiness (result.site_readiness).
export default function SiteReadiness({ readiness }) {
  if (!readiness) return null
  const rows = readinessRows(readiness)
  const total = totalText(readiness)

  return (
    <section className="bars-block" aria-label={SITE_READINESS_TITLE}>
      <div className="bars-heading">
        <h3 className="bars-title">{SITE_READINESS_TITLE}</h3>
        {total && <p className="bars-total">{total}</p>}
      </div>
      {total && (
        <div className="bars">
          {rows.map((row) => (
            <div key={row.key} className="bar-row">
              <span className="bar-name">{row.name}</span>
              <span className="bar-track bar-track-full">
                {row.hasScore && <span className="bar-part bar-part-blue" style={{ width: `${row.fill}%` }} />}
              </span>
              <span className="bar-text">{row.text}</span>
            </div>
          ))}
        </div>
      )}
      <div className="bars-sentence">
        <p>{readiness.sentence}</p>
        {readiness.fix_first.length > 0 && (
          <ul>
            {readiness.fix_first.map((name) => (
              <li key={name}>{name}</li>
            ))}
          </ul>
        )}
      </div>
    </section>
  )
}
