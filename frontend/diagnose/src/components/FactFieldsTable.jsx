// Props: details = fact_consistency's details. One row per fixed field:
// the third-party value with its source link, the official value with
// the exact quote from the company's site, and how each of the runs
// voted. Lets a user check every verdict themselves.
const VERDICT_LABELS = {
  match: 'Agrees',
  conflict: 'Disagrees',
  unknown: 'Not enough information',
}

export default function FactFieldsTable({ details }) {
  const fields = details?.fields
  if (!Array.isArray(fields)) return null

  return (
    <div className="fact-fields">
      {details.voting_rule && <p className="evidence-none">{details.voting_rule}</p>}
      <ul>
        {fields.map((field) => (
          <li key={field.field}>
            <span className="evidence-text">
              {field.label}: {VERDICT_LABELS[field.verdict] || field.verdict}
            </span>
            <span className="evidence-context">
              Review sites: {field.third_party_value || 'not stated'}
              {field.third_party_source && (
                <>
                  {' '}
                  <a href={field.third_party_source} target="_blank" rel="noreferrer">
                    source
                  </a>
                </>
              )}
            </span>
            <span className="evidence-context">
              Your site: {field.official_value || 'not stated'}
              {field.official_quote && <> (“{field.official_quote}”)</>}
            </span>
            <span className="evidence-context">Votes: {(field.votes || []).join(', ')}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
