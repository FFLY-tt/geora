// Numbers and sizes for the bars of "Where AI looked for answers". The backend sends the counts
// (result.source_bars, built by backend/diagnose_service/source_bars.py): per kind of page the
// pages we opened and how many of them mention the company. A row shows three things only: the
// name, a bar and "mentioned of opened". Pages we could not open or did not try are not shown here.
// A kind with no opened page has no row.

export const SOURCE_BARS_TITLE = 'Where AI looked for answers'

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

/** The rows to draw, most opened pages first: { key, name, text, segments }. text is "1 of 29". */
export function barRows(sourceBars) {
  const rows = (sourceBars?.rows || []).filter((row) => row.pages > 0)
  const maxPages = Math.max(0, ...rows.map((row) => row.pages))
  return rows
    .slice()
    .sort((a, b) => b.pages - a.pages)
    .map((row) => ({
      key: row.key,
      name: row.name,
      text: `${row.mention_pages} of ${row.pages}`,
      segments: barSegments(row, maxPages),
    }))
}
