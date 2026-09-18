import { useEffect, useState } from 'react'
import AccordionItem from './AccordionItem'
import EmailGate from './EmailGate'

// Must match the CSS transition durations on .breakdown-content (filter)
// and .breakdown-overlay-unlocking (opacity/transform) in index.html.
const UNLOCK_TRANSITION_MS = 400

// Props: dimensions, unlocked, onUnlock(email).
// onUnlock is required in addition to the two props from the original
// spec because this component is the one that renders EmailGate, so it
// must be the one to forward the unlock callback down to it.
export default function BreakdownAccordion({ dimensions, unlocked, onUnlock }) {
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

  return (
    <div className="breakdown-wrapper">
      <div className={`breakdown-content ${unlocked ? '' : 'breakdown-locked'}`}>
        {dimensions.map((dim) => (
          <AccordionItem key={dim.dimension} dimension={dim} />
        ))}
      </div>

      {showOverlay && (
        <div className={`breakdown-overlay ${unlocked ? 'breakdown-overlay-unlocking' : ''}`}>
          <EmailGate onUnlock={onUnlock} />
        </div>
      )}
    </div>
  )
}
