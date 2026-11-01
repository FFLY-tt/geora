// The top of the result: how many of the answers mention the company, and the range the true
// rate is likely in. citation_visibility is the one number that matters to the reader, shown
// as a real answer count, never as a 0 to 100 score. It never fabricates a number when the
// backend reports insufficient data.
import { headlineText, rangeText } from '../utils/resultPage.js'
import { unavailableText } from '../utils/scoreDisplay.js'

export default function CitationResultCard({ citation }) {
  const hasData = citation && citation.score !== -1

  if (!hasData) {
    // score === -1 (or citation missing entirely): never show 0 or a bar.
    // details.reason is a technical note for us (API names, errors), so
    // the customer sees the plain explanation instead.
    const reason = unavailableText(citation)
    return (
      <div>
        <p className="citation-result-headline citation-result-headline-empty">Not enough data</p>
        <p className="citation-result-subtext">{reason}</p>
      </div>
    )
  }

  // details.mentions and details.total are the count behind the headline; details.ci_low and
  // details.ci_high are the range. Older saved results lack them: the count is then added up
  // from the answers, and no range is shown.
  const range = rangeText(citation.details)

  return (
    <div>
      <h2 className="citation-result-headline">{headlineText(citation.details)}</h2>
      {range && <p className="citation-result-subtext">{range}</p>}
    </div>
  )
}
