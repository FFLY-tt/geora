// "Pages AI read before answering" (part C): which domains AI actually
// cited while answering the brand-free customer questions, read from
// citation_engine.py's details.source_map. Shown only in the unlocked
// part of the report — see BreakdownAccordion, which renders this
// inside breakdown-content so it stays blurred/locked until unlock.
const MAX_DOMAINS_SHOWN = 8

export default function SourceMap({ citation }) {
  const sourceMap = citation?.details?.source_map

  if (!sourceMap || !sourceMap.top_domains || sourceMap.top_domains.length === 0) {
    return (
      <div className="source-map">
        <h3 className="source-map-title">Pages AI read before answering</h3>
        <p className="source-map-empty">No source data for this check</p>
      </div>
    )
  }

  const topDomains = sourceMap.top_domains.slice(0, MAX_DOMAINS_SHOWN)
  const ownSharePercentage = Math.round((sourceMap.own_domain_share || 0) * 100)

  return (
    <div className="source-map">
      <h3 className="source-map-title">Pages AI read before answering</h3>
      <ul className="source-map-list">
        {topDomains.map((entry) => (
          <li
            key={entry.domain}
            className={entry.is_own_domain ? 'source-map-row source-map-row-own' : 'source-map-row'}
          >
            <span className="source-map-domain">
              {entry.domain}
              {entry.is_own_domain && <span className="source-map-own-tag">your site</span>}
            </span>
            <span className="source-map-stats">
              cited in {entry.answers_citing} answer{entry.answers_citing === 1 ? '' : 's'} (
              {Math.round(entry.share * 100)}%)
            </span>
          </li>
        ))}
      </ul>
      <p className="source-map-own-share">Your own site was cited in {ownSharePercentage}% of answers</p>
    </div>
  )
}
