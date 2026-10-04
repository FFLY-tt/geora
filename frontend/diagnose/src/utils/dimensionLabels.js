// Single source of truth for dimension display names and short
// descriptions, keyed by the real dimension names returned by the
// backend (backend/engines/*.py DIMENSION_NAME). Every component that
// needs to show a dimension's label or description must import from
// here instead of maintaining its own copy of this map.
export const DIMENSION_LABELS = {
  citation_visibility: {
    label: 'Citation visibility',
    description: 'How often AI answers mention or cite this brand',
  },
  topic_relevance: {
    label: 'Matches what buyers ask',
    description: 'How directly content answers real customer questions',
  },
  evidence_density: {
    label: 'Real numbers, not hype',
    description: 'How much real data backs up the content',
  },
  content_recency: {
    label: 'Fresh and up to date',
    description: 'How recently key pages were last updated',
  },
  fact_consistency: {
    label: 'Review sites agree with you',
    description: 'Whether key facts match across the web',
  },
  technical_readability: {
    label: 'Easy for AI to read',
    description: 'Whether AI crawlers can reach and understand your pages',
  },
}

/**
 * Display label for a dimension key. Falls back to the raw key itself
 * (e.g. a dimension not yet added to the map above).
 */
export function getDimensionLabel(dimensionKey) {
  return DIMENSION_LABELS[dimensionKey]?.label || dimensionKey
}

/**
 * Short description for a dimension key, or an empty string if it isn't
 * in the map above.
 */
export function getDimensionDescription(dimensionKey) {
  return DIMENSION_LABELS[dimensionKey]?.description || ''
}
