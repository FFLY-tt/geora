import { barRows, SOURCE_BARS_TITLE } from '../utils/sourceBars.js'

// "Where AI looked for answers": one row per kind of page AI read, with three things on it: the
// name, a bar and "mentioned of opened". Blue is the pages that mention you, light gray the opened
// pages that do not. Props: sourceBars (result.source_bars). Nothing is drawn when no kind has an
// opened page.
export default function SourceBars({ sourceBars }) {
  const rows = barRows(sourceBars)
  if (rows.length === 0) return null

  return (
    <section className="bars-block">
      <h3 className="bars-title">{SOURCE_BARS_TITLE}</h3>
      <div className="bars">
        {rows.map((row) => (
          <div key={row.key} className="bar-row">
            <span className="bar-name">{row.name}</span>
            <span className="bar-track">
              <span className="bar-fill" style={{ width: `${row.segments.total}%` }}>
                <span className="bar-part bar-part-blue" style={{ width: `${(row.segments.blue / row.segments.total) * 100}%` }} />
                <span className="bar-part bar-part-gray" style={{ width: `${(row.segments.gray / row.segments.total) * 100}%` }} />
              </span>
            </span>
            <span className="bar-number">{row.text}</span>
            <span className="bar-level" aria-hidden="true" />
          </div>
        ))}
      </div>
    </section>
  )
}
