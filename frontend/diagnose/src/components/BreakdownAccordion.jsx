import { useEffect, useState } from 'react'
import AccordionItem from './AccordionItem'
import EmailGate from './EmailGate'
import SourceMap from './SourceMap'
import SourceLayerTable from './SourceLayerTable'

// Must match the CSS transition durations on .breakdown-content (filter)
// and .breakdown-overlay-unlocking (opacity/transform) in index.html.
const UNLOCK_TRANSITION_MS = 400

// Props: dimensions, sourceLayers, unlocked, onUnlock(email).
// onUnlock is required in addition to the two props from the original
// spec because this component is the one that renders EmailGate, so it
// must be the one to forward the unlock callback down to it.
export default function BreakdownAccordion({ dimensions, sourceLayers, unlocked, onUnlock, sourceBars = null, startOpen = false }) {
  // The lock overlay stays mounted for one extra transition after
  // `unlocked` flips true, so it can animate out (shrink + fade) instead
  // of vanishing the instant the prop changes.
  const [showOverlay, setShowOverlay] = useState(!unlocked)

  useEffect(() => {
    if (!unlocked) {
      setShowOverlay(true)
      return
    }
    const timer = setTimeout(() => setShowOverlay(false), UNLOCK_TRANSITION_MS)
    return () => clearTimeout(timer)
  }, [unlocked])

  // citation_visibility carries details.source_map; SourceMap itself
  // handles citation being absent or score === -1 (shows its own
  // "No source data" state rather than this component guessing).
  const citation = dimensions.find((dim) => dim.dimension === 'citation_visibility')

  return (
    <div className="breakdown-wrapper">
      <div className={`breakdown-content ${unlocked ? '' : 'breakdown-locked'}`}>
        {sourceLayers ? (
          // Layered view: where AI reads, with the older checks filed under their layer.
          <SourceLayerTable sourceLayers={sourceLayers} dimensions={dimensions} sourceBars={sourceBars} startOpen={startOpen} />
        ) : (
          // No layers (AI cited no sources): the plain list of checks.
          <>
            {dimensions.map((dim) => (
              <AccordionItem key={dim.dimension} dimension={dim} startOpen={startOpen} />
            ))}
            <SourceMap citation={citation} />
          </>
        )}
      </div>

      {showOverlay && (
        <div className={`breakdown-overlay ${unlocked ? 'breakdown-overlay-unlocking' : ''}`}>
          <EmailGate onUnlock={onUnlock} />
        </div>
      )}
    </div>
  )
}
