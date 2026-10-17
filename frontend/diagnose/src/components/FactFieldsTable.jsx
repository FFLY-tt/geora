import { factRows } from '../utils/factFields.js'

// Props: details = fact_consistency's details. One row per fixed field: the
// conclusion, what review sites say (with a link to the source), and what
// your own site says (with the exact quote), so a customer can check each
// one. The individual runs and votes are not shown.
export default function FactFieldsTable({ details }) {
  const rows = factRows(details)
  if (rows.length === 0) return null

  return (
    <div className="fact-fields">
      <ul>
        {rows.map((row) => (
          <li key={row.key}>
            <span className="evidence-text">
              {row.label}: {row.verdictText}
            </span>
            <span className="evidence-context">
              Review sites: {row.thirdPartyValue}
              {row.thirdPartySource && (
                <>
                  {' '}
                  <a href={row.thirdPartySource} target="_blank" rel="noreferrer">
                    source
                  </a>
                </>
              )}
            </span>
            <span className="evidence-context">
              Your site: {row.officialValue}
              {row.officialQuote && <> (“{row.officialQuote}”)</>}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}
