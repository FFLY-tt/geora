// Single source of truth for score-to-color/label mapping. Every
// component that needs to show a score's color or band label must import
// getScoreBand from here instead of re-implementing the thresholds.
//
// Colors are CSS variable references (defined in index.html's :root),
// not hardcoded hex values — this file has one job (picking the right
// band), the actual color values live in exactly one place.

const BAND_NOT_ENOUGH_DATA = { color: 'var(--color-ink-muted)', label: 'Not enough data' }
const BAND_GOOD = { color: 'var(--color-success)', label: 'Strong' }
const BAND_OK = { color: 'var(--color-warning)', label: 'Needs work' }
const BAND_WEAK = { color: 'var(--color-danger)', label: 'Weak' }

/**
 * Map a dimension score to a color/label band.
 * score === -1 means "insufficient data", not a real 0.
 */
export function getScoreBand(score) {
  if (score === -1) {
    return BAND_NOT_ENOUGH_DATA
  }
  if (score >= 70) {
    return BAND_GOOD
  }
  if (score >= 40) {
    return BAND_OK
  }
  return BAND_WEAK
}
