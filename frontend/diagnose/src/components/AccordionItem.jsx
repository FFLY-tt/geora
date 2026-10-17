import { useState } from 'react'
import { getDimensionLabel } from '../utils/dimensionLabels'
import { getScoreBand } from '../utils/scoreColor'
import EvidenceList from './EvidenceList'
import FactFieldsTable from './FactFieldsTable'
import { fetchFailureText } from '../utils/scoreDisplay.js'

// The backend's plain_finding is a { what_we_found, what_it_costs_you,
// fix_it_today } object — see backend/engines/_plain_finding_generator.py.
// Each field is pure sentence content with no label inside it; the label
// for each part is added here, once, so only one side of the stack ever
// renders it. A bare string is also accepted for backward compatibility
// with any result still sitting in a 24-hour cache from before this
// field became structured.
const REPORT_PARTS = [
  { key: 'what_we_found', label: 'What we found' },
  { key: 'what_it_costs_you', label: "What it's costing you" },
  { key: 'fix_it_today', label: 'Fix it today' },
]

function getReportParts(dim) {
  const failure = fetchFailureText(dim.details?.fetch_failure)
  if (failure) {
    return [{ label: 'What we found', text: failure }]
  }

  const finding = dim.details?.plain_finding

  if (finding && typeof finding === 'object') {
    return REPORT_PARTS.map(({ key, label }) => ({ label, text: finding[key] })).filter(
      (part) => part.text && part.text.trim(),
    )
  }

  if (typeof finding === 'string' && finding.trim()) {
    return [{ label: 'What we found', text: finding }]
  }

  return [{ label: 'What we found', text: "We couldn't get enough data to evaluate this dimension." }]
}

// Props: dimension = { dimension, score, details }. Starts collapsed.
export default function AccordionItem({ dimension }) {
  const [expanded, setExpanded] = useState(false)

  const label = getDimensionLabel(dimension.dimension)
  const band = getScoreBand(dimension.score)
  const hasScore = dimension.score !== -1

  return (
    <div className="accordion-item">
      <button
        type="button"
        className="accordion-header"
        onClick={() => setExpanded((prev) => !prev)}
        aria-expanded={expanded}
      >
        <span className="accordion-header-label">
          <span>{label}</span>
          <span className="accordion-score" style={{ color: band.color }}>
            {hasScore ? dimension.score : '—'}
          </span>
        </span>
        <span className="accordion-toggle">{expanded ? '−' : '+'}</span>
      </button>

      {expanded && (
        <div className="accordion-body">
          {getReportParts(dimension).map((part) => (
            <p key={part.label}>
              <strong>{part.label}: </strong>
              {part.text}
            </p>
          ))}
          {dimension.dimension === 'evidence_density' && <EvidenceList details={dimension.details} />}
          {dimension.dimension === 'fact_consistency' && <FactFieldsTable details={dimension.details} />}
        </div>
      )}
    </div>
  )
}
