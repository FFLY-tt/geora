// Cleaning for text that comes from outside (page titles, quotes found on a website).
// The page text rules (CLAUDE.md, Customer-facing UI) allow no brackets, no dashes used as
// punctuation and no vertical bars, so text we did not write is cleaned before it is shown.

const BRACKET_GROUP = /[([{][^()[\]{}]*[)\]}]/g
const STRAY_BRACKET = /[()[\]{}]/g
// A separator between a page title and the site name: " | ", " - ", " – ", " — ", " · ", " :: ".
const TITLE_SEPARATOR = /\s+(?:\||–|—|-|·|•|»|::)\s+/

function collapse(text) {
  return text.replace(/\s+/g, ' ').replace(/\s+([,.;:!?])/g, '$1').trim()
}

/** Removes every bracket group together with what is inside it ("9 Tools (2026)" becomes "9 Tools"). */
function withoutBracketGroups(text) {
  let current = text
  for (let i = 0; i < 5; i += 1) {
    const next = current.replace(BRACKET_GROUP, ' ')
    if (next === current) break
    current = next
  }
  return current.replace(STRAY_BRACKET, ' ')
}

/** "https://www.trackingplan.com/blog/x" or "trackingplan.com/blog/x" becomes "trackingplan.com". */
export function siteName(url) {
  const withoutScheme = String(url || '').replace(/^[a-z][a-z0-9+.-]*:\/\//i, '')
  return withoutScheme.split(/[/?#]/)[0].replace(/^www\./i, '')
}

function looksLikeSiteName(part) {
  const words = part.trim().split(/\s+/).filter(Boolean)
  return words.length > 0 && words.length <= 4 && part.trim().length <= 32
}

/**
 * The title of a page for display: no brackets or what is inside them, no site name at
 * the end, no separators left in the middle. Returns "" when nothing is left, and the
 * caller then shows the site address instead.
 *   "Top 8 Cookie Scanner Tools 2026 | Trackingplan"  becomes  "Top 8 Cookie Scanner Tools 2026"
 *   "9 Tools Evaluated (2026)"                         becomes  "9 Tools Evaluated"
 *   "Best CRM Tools – Sybill"                          becomes  "Best CRM Tools"
 */
export function cleanPageTitle(title) {
  let text = collapse(withoutBracketGroups(String(title || '')))
  // "A—B" and "A–B" without spaces are separators too.
  text = text.replace(/\s*[—–]\s*/g, ' – ')
  let parts = text.split(TITLE_SEPARATOR).map((part) => part.trim()).filter(Boolean)
  while (parts.length > 1 && looksLikeSiteName(parts[parts.length - 1])) parts = parts.slice(0, -1)
  text = parts.join(', ')
  text = text.replace(/\s+\/\s+/g, ', ')
  return collapse(text).replace(/^[\s,:;.-]+|[\s,:;-]+$/g, '').trim()
}

/**
 * Text found on a website, shown as it is except for the characters the page text rules
 * do not allow: brackets are dropped and what was inside stays, dashes and bars used as
 * punctuation become commas.
 */
export function plainText(text) {
  return collapse(
    String(text || '')
      .replace(STRAY_BRACKET, ' ')
      .replace(/\s*(?:—|–|--)\s*/g, ', ')
      .replace(/\s+-\s+/g, ', ')
      .replace(/\s*\|\s*/g, ', ')
      .replace(/\s+\/\s+/g, ', ')
      .replace(/(?:,\s*){2,}/g, ', ')
      .replace(/^,\s*|,\s*$/g, ''),
  )
}
