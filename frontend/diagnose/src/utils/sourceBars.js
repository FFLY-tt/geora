// Words and sizes for the bars of "Where AI looked for answers". The backend sends the counts
// (result.source_bars, built by backend/diagnose_service/source_bars.py): per kind of website the
// pages we opened, how many mention the company, and how many could not be opened or were not
// tried. This file turns them into the sentence on the right of each bar and the length of each
// part of the bar. Nothing here is counted again from the raw data.

export const SOURCE_BARS_TITLE = 'Where AI looked for answers'

export function sourceBarsIntro(answerCount) {
  return `We asked AI ${answerCount} questions your customers ask. To answer them, AI read these kinds of pages.`
}

function pages(count) {
  return `${count} ${count === 1 ? 'page' : 'pages'}`
}

/** The sentence on the right of a bar. */
export function barText(row) {
  if (row.key === 'own_site') return `AI read ${pages(row.pages)} from your website.`
  if (row.pages === 0) {
    return row.could_not_open > 0 ? 'We could not open these pages.' : 'We did not check these pages.'
  }
  if (row.pages === 1) return row.mention_pages === 1 ? 'This page mentions you.' : 'This page does not mention you.'
  if (row.mention_pages === 0) return `None of these ${row.pages} pages mention you`
  if (row.mention_pages === row.pages) return `All ${row.pages} pages mention you`
  if (row.mention_pages === 1) return `1 of ${row.pages} pages mentions you`
  return `${row.mention_pages} of ${row.pages} pages mention you`
}

/** The small lines under the sentence, about pages that are not in the bar. */
export function barNotes(row) {
  const notes = []
  if (row.pages > 0 && row.could_not_open > 0) notes.push(`We could not open ${row.could_not_open} more ${row.could_not_open === 1 ? 'page' : 'pages'}.`)
  if (row.not_checked > 0 && (row.pages > 0 || row.could_not_open > 0)) {
    notes.push(`We did not check ${row.not_checked} more ${row.not_checked === 1 ? 'page' : 'pages'}.`)
  }
  return notes
}

/**
 * How much of the track each part of a bar takes, in percent of the track. The longest bar fills
 * the track; the blue part is the pages that mention you, the gray part the opened pages that do
 * not. A row with no opened page has no bar.
 */
export function barSegments(row, maxPages) {
  if (!row.pages || !maxPages) return { total: 0, blue: 0, gray: 0 }
  const total = (row.pages / maxPages) * 100
  const blue = (row.mention_pages / row.pages) * total
  return { total, blue, gray: total - blue }
}

/** The rows to draw, in the order the backend sent them: sorted by pages, most first. */
export function barRows(sourceBars) {
  return (sourceBars?.rows || []).map((row) => ({
    key: row.key,
    name: row.name,
    text: barText(row),
    notes: barNotes(row),
    segments: barSegments(row, sourceBars.max_pages),
  }))
}
