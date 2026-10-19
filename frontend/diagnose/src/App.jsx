import { useState } from 'react'
import Hero from './components/Hero'
import SearchForm from './components/SearchForm'
import ResultPage from './components/ResultPage'
import { runDiagnosis, captureLead } from './api'

// How long the one-time post-unlock scan-sweep highlight plays for. Must
// match the .scan-sweep-once animation duration in index.html.
const SWEEP_DURATION_MS = 800

// App only manages state and wires components together. No business
// logic or styling detail lives here — that belongs in api.js / the
// components themselves.
export default function App() {
  const [companyName, setCompanyName] = useState('')
  const [domain, setDomain] = useState('')
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [runError, setRunError] = useState('')
  const [unlocked, setUnlocked] = useState(false)
  // Not in the original state list, but required to surface a failed
  // unlock attempt without pretending it succeeded (see handleUnlock).
  const [unlockError, setUnlockError] = useState('')
  // Briefly true right after a successful unlock, to trigger the
  // one-time scan-sweep highlight over the conclusion / citation card.
  // Never reused for any other transition in the app.
  const [justUnlocked, setJustUnlocked] = useState(false)

  async function handleRunDiagnosis(submittedCompanyName, submittedDomain, submittedCompetitors) {
    setCompanyName(submittedCompanyName)
    setDomain(submittedDomain)
    setLoading(true)
    setRunError('')
    setUnlocked(false)
    setUnlockError('')

    try {
      const diagnosisResult = await runDiagnosis(submittedCompanyName, submittedDomain, submittedCompetitors)
      setResult(diagnosisResult)
    } catch (error) {
      setRunError(error.message)
      setResult(null)
    } finally {
      setLoading(false)
    }
  }

  async function handleUnlock(email) {
    if (!result) return

    setUnlockError('')

    try {
      await captureLead(email, result.company_name, result.domain, result.dimensions)
      setUnlocked(true)
      setJustUnlocked(true)
      setTimeout(() => setJustUnlocked(false), SWEEP_DURATION_MS)
    } catch (error) {
      // Lead was not actually stored, so do not pretend the report unlocked.
      setUnlockError(error.message)
    }
  }

  return (
    <div className="page">
      <section className="section">
        <Hero />
      </section>

      <section className="section">
        <SearchForm onSubmit={handleRunDiagnosis} loading={loading} />
        {runError && <p className="form-error">{runError}</p>}
      </section>

      {result && (
        <ResultPage
          result={result}
          unlocked={unlocked}
          onUnlock={handleUnlock}
          unlockError={unlockError}
          justUnlocked={justUnlocked}
        />
      )}
    </div>
  )
}
