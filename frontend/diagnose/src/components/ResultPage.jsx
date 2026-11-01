import { useState } from 'react'
import CacheNotice from './CacheNotice'
import CitationResultCard from './CitationResultCard'
import CompetitorList from './CompetitorList'
import SourceBars from './SourceBars'
import SiteReadiness from './SiteReadiness'
import BreakdownAccordion from './BreakdownAccordion'

// The whole result page for one diagnosis, top to bottom, and nothing else: the 24 hour notice, the
// headline and its likely range, the competitor list (only when competitors were entered), the bars
// of where AI looked, the bars of how ready the website is, and a "Show details" button. The
// details behind the button stay locked until an email is entered.
//
// Props: result (the API response), unlocked, onUnlock(email), unlockError, justUnlocked.
// detailsOpen and startOpen only set the starting state: the page text test renders it with
// the details open and every row expanded, so it can read all of it.
export default function ResultPage({
  result,
  unlocked,
  onUnlock,
  unlockError = '',
  justUnlocked = false,
  detailsOpen: startDetailsOpen = false,
  startOpen = false,
}) {
  const [detailsOpen, setDetailsOpen] = useState(startDetailsOpen)
  const citation = result.dimensions.find((dim) => dim.dimension === 'citation_visibility')

  return (
    <>
      <section className="section">
        <CacheNotice />
        <div className={justUnlocked ? 'scan-sweep-once' : ''}>
          <CitationResultCard citation={citation} />
          <CompetitorList comparison={result.competitor_comparison} />
          <SourceBars sourceBars={result.source_bars} />
          <SiteReadiness readiness={result.site_readiness} />
        </div>
        <button type="button" className="btn-mechanical details-toggle" onClick={() => setDetailsOpen((open) => !open)} aria-expanded={detailsOpen}>
          {detailsOpen ? 'Hide details' : 'Show details'}
        </button>
      </section>

      {detailsOpen && (
        <section className="section">
          <BreakdownAccordion
            dimensions={result.dimensions}
            sourceLayers={result.source_layers}
            sourceBars={result.source_bars}
            unlocked={unlocked}
            onUnlock={onUnlock}
            startOpen={startOpen}
          />
          {unlockError && <p className="form-error">{unlockError}</p>}
        </section>
      )}
    </>
  )
}
