import { describeVersions } from '../utils/provenance.js'

// One line of small print at the bottom of the result page saying which
// method versions and which buyer question set version produced it.
export default function ProvenanceFooter({ provenance }) {
  if (!provenance) return null
  return <p className="provenance-footer">{describeVersions(provenance)}</p>
}
