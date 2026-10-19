// Words and sizes for "Is your website ready for AI?". The backend sends the scores, the
// levels and the closing sentence (result.site_readiness, built by
// backend/diagnose_service/site_readiness.py). The level is always written as a word, never only
// shown by color.

export const SITE_READINESS_TITLE = 'Is your website ready for AI?'

/** "70 out of 100", or null when no score could be worked out. */
export function totalText(readiness) {
  return readiness && readiness.total !== null && readiness.total !== undefined ? `${readiness.total} out of 100` : null
}

/** The rows to draw: name, the filled part of the bar in percent, and the text on the right. */
export function readinessRows(readiness) {
  return (readiness?.items || []).map((item) => ({
    key: item.key,
    name: item.name,
    hasScore: item.score !== null && item.score !== undefined,
    fill: item.score === null || item.score === undefined ? 0 : Math.max(0, Math.min(100, item.score)),
    text: item.score === null || item.score === undefined ? 'Not checked' : `${item.score} ${item.level}`,
  }))
}
