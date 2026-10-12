import { useState } from 'react'

// Display names for the four evidence categories evidence_density returns.
const CATEGORY_LABELS = {
  number_or_statistic: 'Specific numbers',
  attributed_quote: 'Named customer testimonials',
  customer_stories: 'Customer story pages',
  third_party_source_or_certification: 'Third-party ratings and certifications',
}

const PAGE_LABELS = {
  home: 'Homepage',
  customers: 'Customers page',
  security: 'Security page',
  pricing: 'Pricing page',
  about: 'About page',
}

// Props: details = evidence_density's details. Renders every piece of
// evidence the code rules found, with the original wording and the page
// it came from, so a user can check each one themselves.
export default function EvidenceList({ details }) {
  const [open, setOpen] = useState(false)
  // The homepage could not be fetched at all (e.g. "blocked: HTTP 403"):
  // say so plainly instead of showing an empty evidence list or a score.
  if (details?.fetch_failure) {
    return (
      <p className="evidence-none">
        We couldn't read this website. Reason: {details.fetch_failure}.
      </p>
    )
  }
  const items = details?.items
  if (!Array.isArray(items)) return null

  return (
    <div className="evidence-list">
      <button type="button" className="evidence-list-toggle" onClick={() => setOpen((prev) => !prev)} aria-expanded={open}>
        {open ? 'Hide' : 'Show'} the {items.length} piece{items.length === 1 ? '' : 's'} of evidence we found
      </button>
      {open &&
        Object.entries(CATEGORY_LABELS).map(([category, label]) => {
          const group = items.filter((item) => item.category === category)
          return (
            <div key={category} className="evidence-group">
              <p className="evidence-group-title">
                {label}: {group.length}
              </p>
              {group.length === 0 && <p className="evidence-none">None found.</p>}
              <ul>
                {group.map((item) => (
                  <li key={`${item.category}-${item.key}`}>
                    <span className="evidence-text">{item.text}</span>
                    <span className="evidence-context">{item.context}</span>
                    <a className="evidence-source" href={item.source_url} target="_blank" rel="noreferrer">
                      {PAGE_LABELS[item.source_page] || item.source_page}: {item.source_url}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          )
        })}
      {open && Array.isArray(details.customer_logos_reference) && details.customer_logos_reference.length > 0 && (
        <div className="evidence-group">
          <p className="evidence-group-title">Customer logos we found (not scored, for reference only)</p>
          <p className="evidence-none">{details.customer_stories_reason}</p>
          <ul>
            {details.customer_logos_reference.map((item) => (
              <li key={`logo-${item.key}`}>
                <span className="evidence-text">{item.text}</span>
                <span className="evidence-context">{PAGE_LABELS[item.source_page] || item.source_page}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
