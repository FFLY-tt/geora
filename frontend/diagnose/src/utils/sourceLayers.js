// Customer-facing wording and small helpers for the layered diagnosis
// (result.source_layers, built by backend/diagnose_service/source_layers.py).
// The backend sends numbers and status codes; every word the customer
// reads is decided here, and internal details (list versions, method
// versions, raw error names) are never shown.

export const LAYER_LABELS = {
  reference: 'Reference sources',
  review_media: 'Review sites and media',
  community: 'Community sites',
  own_site: 'Your website',
  other_companies: "Other companies' websites",
}

export const LAYER_HINTS = {
  reference: 'Wikipedia, standards bodies, government and university sites',
  review_media: 'G2, Capterra, Gartner and tech or industry media',
  community: 'Reddit, LinkedIn, YouTube, Quora and similar',
  own_site: 'Your own domain and its subdomains',
  other_companies: 'Competitors and other companies, including their blogs and comparison pages',
}

export const STATUS_LABELS = {
  biggest_gap: 'Biggest gap',
  gap: 'Gap',
  covered: 'Covered',
  not_used: "AI didn't use this",
  unknown: "Couldn't check",
}

// Which of the older diagnostic dimensions are shown inside which layer.
export const DIMENSIONS_BY_LAYER = {
  review_media: ['fact_consistency'],
  own_site: ['topic_relevance', 'evidence_density', 'content_recency', 'technical_readability'],
}

/** 0.4 -> "40%"; null/undefined -> "—" (not measurable). */
export function formatPercent(value) {
  if (value === null || value === undefined) return '—'
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
  return page.mentions_you ? 'Mentions you' : "Doesn't mention you"
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

/** "cited in 3 answers" / "cited in 1 answer" */
export function citedText(count) {
  return `cited in ${count} answer${count === 1 ? '' : 's'}`
}

/**
 * Rows for the layer table, in the order the backend sent them. Layers AI
 * never cited are still listed (the customer sees AI doesn't use them).
 */
export function layerRows(sourceLayers) {
  return (sourceLayers?.layers || []).map((layer) => ({
    ...layer,
    label: LAYER_LABELS[layer.layer] || layer.layer,
    hint: LAYER_HINTS[layer.layer] || '',
    statusText: STATUS_LABELS[layer.status] || '',
    relianceText: formatPercent(layer.reliance),
    // A layer AI doesn't cite has nothing to be present in.
    presenceText: layer.citations === 0 ? '—' : formatPercent(layer.presence),
  }))
}
