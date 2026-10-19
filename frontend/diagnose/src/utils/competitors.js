// Competitor input and display helpers. The backend does the real parsing
// and counting; this only stops an obviously too long list before it is sent
// and turns the comparison block into the lines the page shows.

export const MAX_COMPETITORS = 5

/** How many competitors the text holds (comma separated, blanks ignored). */
export function countCompetitors(text) {
  return String(text || '')
    .split(/[,，;；\n]/)
    .filter((item) => item.trim()).length
}

/** The list lines for the result page: [{ key, name, isYou, text }], in the order the backend sent (most mentions first). */
export function comparisonLines(comparison) {
  if (!comparison || !Array.isArray(comparison.entries)) return []
  return comparison.entries.map((entry) => ({
    key: `${entry.name}|${entry.domain || ''}`,
    name: entry.name,
    isYou: Boolean(entry.is_you),
    text: `${entry.mentions} of ${entry.total}`,
  }))
}
