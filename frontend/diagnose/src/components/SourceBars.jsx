import { barRows, SOURCE_BARS_TITLE, sourceBarsIntro } from '../utils/sourceBars.js'

// "Where AI looked for answers": one row per kind of page AI read, with a bar for the pages we
// opened. Blue is the part that mentions you, light gray the part that does not. The words on
// the right say it again in numbers, so there is no legend. Props: sourceBars (result.source_bars)
// and answerCount (how many answers the question round gave).
export default function SourceBars({ sourceBars, answerCount }) {
  const rows = barRows(sourceBars)
  if (rows.length === 0) return null

  return (
    <section className="bars-block" aria-label={SOURCE_BARS_TITLE}>
      <h3 className="bars-title">{SOURCE_BARS_TITLE}</h3>
      <p className="bars-intro">{sourceBarsIntro(answerCount)}</p>
      <div className="bars">
        {rows.map((row) => (
          <div key={row.key} className="bar-row">
            <span className="bar-name">{row.name}</span>
            <span className="bar-track">
              {row.segments.total > 0 && (
                <span className="bar-fill" style={{ width: `${row.segments.total}%` }}>
                  <span className="bar-part bar-part-blue" style={{ width: `${(row.segments.blue / row.segments.total) * 100}%` }} />
                  <span className="bar-part bar-part-gray" style={{ width: `${(row.segments.gray / row.segments.total) * 100}%` }} />
                </span>
              )}
            </span>
            <span className="bar-text">
              {row.text}
              {row.notes.map((note) => (
                <span key={note} className="bar-note">
                  {note}
                </span>
              ))}
            </span>
          </div>
        ))}
      </div>
    </section>
  )
}
