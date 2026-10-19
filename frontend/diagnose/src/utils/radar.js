// Words and numbers for the five-corner chart. The backend sends the numbers (result.radar,
// built by backend/diagnose_service/radar.py): per corner gray, blue and a status. This file
// gives every corner its customer wording and turns the numbers into the sentence shown when
// the reader points at a corner. Nothing here is computed again from the raw data.

// The corners, in the order the backend sends them.
export const RADAR_LABELS = {
  lists: 'Best tools lists',
  guides: 'Guides and articles',
  review_media: 'Review sites and news',
  community: 'Community discussions',
  own_site: 'Your website',
}

// The same names inside a sentence.
const RADAR_PHRASES = {
  lists: 'best tools lists',
  guides: 'guides and articles',
  review_media: 'review sites and news',
  community: 'community discussions',
  own_site: 'your website',
}

export const RADAR_LEGEND = {
  gray: 'Where AI looks for answers',
  blue: 'Where AI finds you',
}

// Small text under a corner name.
export const RADAR_NOTES = {
  not_used: 'Not used',
  could_not_check: 'Could not check',
}

const PER_HUNDRED = 'out of every 100 sources'

function whole(value) {
  return Math.round(value)
}

/** The sentence shown for one corner, for example "AI used guides and articles for 55 out of every 100 sources. None of them mention you." */
export function radarSentence(axis) {
  const phrase = RADAR_PHRASES[axis.key] || 'this kind of website'
  if (axis.status === 'not_used') return `AI did not use ${phrase}.`

  const gray = whole(axis.gray)
  const used = `AI used ${phrase} for ${gray === 0 ? 'fewer than 1' : gray} ${PER_HUNDRED}.`
  if (axis.status === 'could_not_check') return `${used} We could not open these pages to check them.`

  const blue = whole(axis.blue)
  if (axis.blue <= 0) return `${used} None of them mention you.`
  if (blue === 0) return `${used} Only a very few of them mention you.`
  if (blue >= gray) return `${used} All of them mention you.`
  return `${used} About ${blue} of those ${gray} mention you.`
}

/** The corners for the chart and its text: [{ key, label, note, gray, blue, status, sentence }]. */
export function radarCorners(radar) {
  return (radar?.axes || []).map((axis) => ({
    key: axis.key,
    label: RADAR_LABELS[axis.key] || axis.key,
    note: RADAR_NOTES[axis.status] || '',
    gray: axis.gray,
    blue: axis.blue,
    status: axis.status,
    sentence: radarSentence(axis),
  }))
}

/** The numbers to draw: both shapes and the one scale all corners share. */
export function radarChartData(radar) {
  const corners = radarCorners(radar)
  return {
    corners,
    labels: corners.map((corner) => corner.label),
    gray: corners.map((corner) => corner.gray),
    blue: corners.map((corner) => corner.blue),
    scaleMax: radar?.scale_max || 10,
  }
}

/** Splits a sentence into lines of about `width` characters, for the pointer tooltip. */
export function wrapLines(text, width = 38) {
  const lines = []
  let line = ''
  for (const word of String(text).split(' ')) {
    if (line && `${line} ${word}`.length > width) {
      lines.push(line)
      line = word
    } else {
      line = line ? `${line} ${word}` : word
    }
  }
  if (line) lines.push(line)
  return lines
}
