// Customer-facing wording and small helpers for the layered diagnosis
// (result.source_layers, built by backend/diagnose_service/source_layers.py).
// The backend sends numbers and status codes; every word the customer
// reads is decided here, and internal details (list versions, method
// versions, raw error names) are never shown.
import { cleanPageTitle } from './text.js'

export const LAYER_LABELS = {
  reference: 'Reference sites',
  review_media: 'Review sites and news',
  community: 'Community discussions',
  own_site: 'Your website',
  lists: 'Best tools lists',
  guides: 'Guides and articles',
  product_pages: 'Competitor product pages',
  other_companies: "Other companies' websites",
}

export const LAYER_HINTS = {
  reference: 'Wikipedia, standards bodies, government and university sites',
  review_media: 'G2, Capterra, Gartner and tech or industry media',
  community: 'Reddit, LinkedIn, YouTube, Quora and similar',
  own_site: 'Your own domain and its subdomains',
  lists: 'Best-of lists, comparisons and alternatives pages written by other companies',
  guides: 'Blog posts, how-to guides, documentation and help articles from other companies',
  product_pages: "Other companies' own product, pricing and landing pages",
  other_companies: 'Competitors and other companies',
}

export const STATUS_LABELS = {
  biggest_gap: 'Missing most',
  gap: 'Missing',
  covered: 'Covered',
  not_used: "AI didn't use this",
  unknown: "Couldn't check",
}

// Which of the older diagnostic dimensions are shown inside which layer.
export const DIMENSIONS_BY_LAYER = {
  review_media: ['fact_consistency'],
  own_site: ['topic_relevance', 'evidence_density', 'content_recency', 'technical_readability'],
}

/** 0.4 -> "40%"; null/undefined -> "Not checked" (nothing could be measured). */
export function formatPercent(value) {
  if (value === null || value === undefined) return 'Not checked'
  return `${Math.round(value * 100)}%`
}

/** The dimension objects that belong under `layer`, in the fixed display order. */
export function dimensionsForLayer(layer, dimensions) {
  const wanted = DIMENSIONS_BY_LAYER[layer] || []
  return wanted.map((name) => dimensions.find((dim) => dim.dimension === name)).filter(Boolean)
}

/** Dimensions not shown under any layer (the outcome card shows citation_visibility itself). */
export function unplacedDimensions(dimensions) {
  const placed = new Set(Object.values(DIMENSIONS_BY_LAYER).flat())
  return dimensions.filter((dim) => dim.dimension !== 'citation_visibility' && !placed.has(dim.dimension))
}

/** What to say about one cited page. */
export function pageStatusText(layer, page) {
  if (layer === 'own_site') return 'Your site'
  if (page.status === 'unknown') return "Couldn't read this page"
  if (page.status === 'not_checked') return 'Not checked'
  return page.mentions_you ? 'Mentions you' : "Doesn't mention you"
}

/** The text to show for a cited page: its cleaned title when we have one, otherwise its address. */
export function pageTitle(page) {
  return cleanPageTitle(page.title) || shortUrl(page.url)
}

/** A short readable form of a citation URL (the backend sends host + path). */
export function shortUrl(url) {
  const withoutScheme = url.replace(/^https?:\/\//, '')
  return withoutScheme.length > 70 ? `${withoutScheme.slice(0, 67)}...` : withoutScheme
}

/** Full link for a stored host + path URL. */
export function pageHref(url) {
  return /^https?:\/\//.test(url) ? url : `https://${url}`
}

/** "used in 3 answers" / "used in 1 answer" */
export function citedText(count) {
  return `used in ${count} answer${count === 1 ? '' : 's'}`
}

function statusForChart(layer, radar) {
  if (radar.biggest_gap === layer.layer) return 'biggest_gap'
  return layer.status === 'biggest_gap' ? 'gap' : layer.status
}

/**
 * Rows for the layer table, in the order the backend sent them. Layers AI
 * never cited are still listed (the customer sees AI doesn't use them).
 */
export function layerRows(sourceLayers, radar = null) {
  return (sourceLayers?.layers || []).map((layer) => ({
    ...layer,
    // "Missing most" belongs to the corner of the chart where the most is missing, so the table
    // and the chart agree. The kinds of website without a corner can only be "Missing".
    status: radar ? statusForChart(layer, radar) : layer.status,
    label: LAYER_LABELS[layer.layer] || layer.layer,
    hint: LAYER_HINTS[layer.layer] || '',
    statusText: STATUS_LABELS[radar ? statusForChart(layer, radar) : layer.status] || '',
    relianceText: formatPercent(layer.reliance),
    // A layer AI doesn't cite has nothing to be present in.
    presenceText: layer.citations === 0 ? 'Not used' : formatPercent(layer.presence),
  }))
}
