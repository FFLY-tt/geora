// "Pages AI read before answering" (part C): which domains AI actually
// cited while answering the brand-free customer questions, read from
// citation_engine.py's details.source_map. Shown only in the unlocked
// part of the report — see BreakdownAccordion, which renders this
// inside breakdown-content so it stays blurred/locked until unlock.
//
// Inline styles rather than new index.html CSS rules: this file lives
// under frontend/diagnose/src, which is the only part of this app this
// change is allowed to touch. Colors/fonts still come from index.html's
// existing CSS variables so this matches the rest of the page.
const MAX_DOMAINS_SHOWN = 8

const titleStyle = {
  fontFamily: 'var(--font-body)',
  fontSize: '1rem',
  fontWeight: 600,
  margin: '0 0 12px',
  color: 'var(--color-ink)',
}

const emptyStyle = {
  color: 'var(--color-ink-muted)',
  fontSize: '0.9rem',
  margin: 0,
}

const listStyle = {
  listStyle: 'none',
  margin: 0,
  padding: 0,
  display: 'flex',
  flexDirection: 'column',
  gap: 8,
}

function rowStyle(isOwnDomain) {
  return {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'baseline',
    gap: 12,
    padding: isOwnDomain ? '6px 10px' : '6px 0',
    borderRadius: 5,
    background: isOwnDomain ? 'rgba(52, 82, 255, 0.08)' : 'transparent',
    border: isOwnDomain ? '1px solid var(--color-accent)' : 'none',
  }
}

const domainStyle = {
  fontFamily: 'var(--font-mono)',
  fontSize: '0.9rem',
}

const ownTagStyle = {
  color: 'var(--color-accent)',
  fontFamily: 'var(--font-body)',
  fontSize: '0.75rem',
  marginLeft: 8,
}

const statsStyle = {
  color: 'var(--color-ink-muted)',
  fontSize: '0.85rem',
  whiteSpace: 'nowrap',
}

const ownShareStyle = {
  color: 'var(--color-ink-muted)',
  fontSize: '0.85rem',
  marginTop: 16,
  marginBottom: 0,
}

export default function SourceMap({ citation }) {
  const sourceMap = citation?.details?.source_map

  if (!sourceMap || !sourceMap.top_domains || sourceMap.top_domains.length === 0) {
    return (
      <div style={{ marginTop: 32 }}>
        <h3 style={titleStyle}>Pages AI read before answering</h3>
        <p style={emptyStyle}>No source data for this check</p>
      </div>
    )
  }

  const topDomains = sourceMap.top_domains.slice(0, MAX_DOMAINS_SHOWN)
  const ownSharePercentage = Math.round((sourceMap.own_domain_share || 0) * 100)

  return (
    <div style={{ marginTop: 32 }}>
      <h3 style={titleStyle}>Pages AI read before answering</h3>
      <ul style={listStyle}>
        {topDomains.map((entry) => (
          <li key={entry.domain} style={rowStyle(entry.is_own_domain)}>
            <span style={domainStyle}>
              {entry.domain}
              {entry.is_own_domain && <span style={ownTagStyle}>your site</span>}
            </span>
            <span style={statsStyle}>
              cited in {entry.answers_citing} answer{entry.answers_citing === 1 ? '' : 's'} (
              {Math.round(entry.share * 100)}%)
            </span>
          </li>
        ))}
      </ul>
      <p style={ownShareStyle}>Your own site was cited in {ownSharePercentage}% of answers</p>
    </div>
  )
}
