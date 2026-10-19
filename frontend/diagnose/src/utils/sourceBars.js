// Numbers and sizes for the bars of "Where AI looked for answers". The backend sends the counts
// (result.source_bars, built by backend/diagnose_service/source_bars.py): per kind of page the
// pages we opened and how many of them mention the company. A row shows three things only: the
// name, a bar and "mentioned of opened". Pages we could not open or did not try are not shown here.
//
// The other kinds of page have a row only when they have an opened page, most pages first.
// "Your own website" always has a row and is always the last one: when AI read pages from it the
// bar is all blue and reads "2 of 2"; when AI never used it the bar is empty and the number is
// "0", because AI not reading your website is itself the answer.

export const SOURCE_BARS_TITLE = 'Where AI looked for answers'

const OWN_SITE = 'own_site'
const OWN_SITE_NAME = 'Your own website'

/**
 * How much of the track each part of a bar takes, in percent of the track. The longest bar fills
 * the track; blue is the pages that mention you, light gray the opened pages that do not.
 */
export function barSegments(row, maxPages) {
  if (!row.pages || !maxPages) return { total: 0, blue: 0, gray: 0 }
  const total = (row.pages / maxPages) * 100
  const blue = (row.mention_pages / row.pages) * total
  return { total, blue, gray: total - blue }
}

/**
 * The rows to draw: { key, name, text, segments, empty }. text is "1 of 29", or "0" for your own
 * website when AI never used it (then empty is true and the bar is an empty gray track).
 * Nothing is returned when the response has no source bars at all (AI used no sources).
 */
export function barRows(sourceBars) {
  if (!sourceBars) return []
  const rows = sourceBars.rows || []
  const others = rows.filter((row) => row.key !== OWN_SITE && row.pages > 0).sort((a, b) => b.pages - a.pages)
  const own = rows.find((row) => row.key === OWN_SITE && row.pages > 0)
  const maxPages = Math.max(0, ...others.map((row) => row.pages), own ? own.pages : 0)

  const drawn = others.map((row) => ({
    key: row.key,
    name: row.name,
    text: `${row.mention_pages} of ${row.pages}`,
    segments: barSegments(row, maxPages),
    empty: false,
  }))
  drawn.push(
    own
      ? { key: OWN_SITE, name: own.name, text: `${own.pages} of ${own.pages}`, segments: barSegments({ ...own, mention_pages: own.pages }, maxPages), empty: false }
      : { key: OWN_SITE, name: OWN_SITE_NAME, text: '0', segments: { total: 0, blue: 0, gray: 0 }, empty: true },
  )
  return drawn
}
