// The outcome card (part A of the results page): citation_visibility is
// the one metric that actually matters to the reader, shown as a real
// answer-level mention rate, never as a 0-100 "score" alongside the 4
// reason dimensions below it. Reads only real fields from
// citation_engine.py's DimensionScore.details — see each inline
// comment for exactly which ones, and never fabricates a number when
// the backend reports insufficient data.
export default function CitationResultCard({ citation }) {
  const hasData = citation && citation.score !== -1

  if (!hasData) {
    // score === -1 (or citation missing entirely): details.reason is
    // the backend's own explanation of why — never show 0 or a bar.
    const reason = citation?.details?.reason || 'We could not run this check.'
    return (
      <div>
        <p style={headlineStyle(true)}>Not enough data</p>
        <p style={subtextStyle}>{reason}</p>
      </div>
    )
  }

  // details.queries_total: how many brand-free customer questions were
  // tested (normally 5). details.runs_per_query: how many separate
  // times each question was asked (3). details.per_query_results: one
  // entry per question, each with a "runs" list — every run carries
  // its own status ("ok"/"error") and is_hit. The answer-level mention
  // rate shown here (hitCount of totalCount) is computed directly from
  // those real per-run results, not from the question-level average
  // that produces the rounded citation_visibility score itself — the
  // two are different views of the same data and can differ slightly.
  const { queries_total: queriesTotal, runs_per_query: runsPerQuery, per_query_results: perQueryResults = [] } =
    citation.details

  let hitCount = 0
  let totalCount = 0
  for (const entry of perQueryResults) {
    for (const run of entry.runs || []) {
      if (run.status === 'ok') {
        totalCount += 1
        if (run.is_hit) hitCount += 1
      }
    }
  }

  const percentage = totalCount > 0 ? Math.round((hitCount / totalCount) * 100) : 0

  return (
    <div>
      <p style={headlineStyle(false)}>
        AI mentioned you in {hitCount} of {totalCount} answers ({percentage}%)
      </p>
      <p style={subtextStyle}>
        {queriesTotal} questions, each asked {runsPerQuery} times. AI answers change between asks, so we
        report how often, not yes or no.
      </p>
    </div>
  )
}

// Inline styles, not a new index.html rule: this component lives under
// frontend/diagnose/src, which is the only part of this app this
// change is allowed to touch. Colors/fonts still come from index.html's
// existing CSS variables so this matches the rest of the page.
function headlineStyle(isEmpty) {
  return {
    fontFamily: 'var(--font-headline)',
    fontWeight: 600,
    fontSize: isEmpty ? '1.5rem' : 'clamp(1.5rem, 4.2vw, 2.1rem)',
    lineHeight: 1.2,
    margin: '0 0 8px',
    color: isEmpty ? 'var(--color-ink-muted)' : 'var(--color-ink)',
  }
}

const subtextStyle = {
  fontFamily: 'var(--font-body)',
  color: 'var(--color-ink-muted)',
  fontSize: '0.85rem',
  lineHeight: 1.5,
  margin: 0,
}
