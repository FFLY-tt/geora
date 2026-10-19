// Numbers and sizes for "Is your website ready for AI?". The backend sends the scores and the
// levels (result.site_readiness, built by backend/diagnose_service/site_readiness.py). A row
// shows four things: the name, a bar as long as the score out of 100, the score and the level
// as a word. A check with no result has no row, and when no check has a result the whole block
// is left out.

export const SITE_READINESS_TITLE = 'Is your website ready for AI?'

/** The rows to draw: { key, name, fill, score, level }, only for checks that have a score. */
export function readinessRows(readiness) {
  return (readiness?.items || [])
    .filter((item) => item.score !== null && item.score !== undefined)
    .map((item) => ({
      key: item.key,
      name: item.name,
      fill: Math.max(0, Math.min(100, item.score)),
      score: String(item.score),
      level: item.level,
    }))
}

/** "60 out of 100", or null when there is nothing to show. */
export function totalText(readiness) {
  if (readinessRows(readiness).length === 0) return null
  return readiness.total === null || readiness.total === undefined ? null : `${readiness.total} out of 100`
}
