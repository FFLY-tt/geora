import { useState } from 'react'
import CacheNotice from './CacheNotice'
import ScoreChangeNote from './ScoreChangeNote'
import CitationResultCard from './CitationResultCard'
import CompetitorList from './CompetitorList'
import SourceBars from './SourceBars'
import SiteReadiness from './SiteReadiness'
import GapConclusion from './GapConclusion'
import BreakdownAccordion from './BreakdownAccordion'
import { mentionCounts } from '../utils/resultPage.js'

// The whole result page for one diagnosis, top to bottom: the headline and its range, the
// competitor list (only when competitors were entered), the bars of where AI looked, the one
// sentence conclusion with its pages, how ready the website is, and a "Show details" button. Everything above the button is free to read;
// the details behind it stay locked until an email is entered.
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
  const answerCount = mentionCounts(citation?.details).total || 15

  return (
    <>
      <section className="section">
        <CacheNotice />
        <ScoreChangeNote
          previousScores={result.previous_scores}
          currentScores={result.dimensions}
          previousProvenance={result.previous_provenance}
          currentProvenance={result.provenance}
          previousCitationInterval={result.previous_citation_interval}
          currentCitationInterval={citation?.details}
        />
        <div className={justUnlocked ? 'scan-sweep-once' : ''}>
          <CitationResultCard citation={citation} />
          <CompetitorList comparison={result.competitor_comparison} />
          <SourceBars sourceBars={result.source_bars} answerCount={answerCount} />
          <GapConclusion sourceBars={result.source_bars} sourceLayers={result.source_layers} />
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
