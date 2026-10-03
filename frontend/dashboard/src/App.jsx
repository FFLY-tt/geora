import { useState } from 'react'
import { fetchAnswerCapsules, fetchTechnicalPackage } from './api'

// P0 scaffold: technical package (fast, rule-based) and Answer Capsules
// (slow, LLM-backed) are two independent sections with their own button
// and loading state on purpose — they hit two separate backend routes
// specifically so one never blocks the other. No shared styling system
// yet; this is functional, not a visual pass.
export default function App() {
  const [companyName, setCompanyName] = useState('')
  const [domain, setDomain] = useState('')

  const [technicalPackage, setTechnicalPackage] = useState(null)
  const [technicalPackageLoading, setTechnicalPackageLoading] = useState(false)
  const [technicalPackageError, setTechnicalPackageError] = useState('')

  const [capsuleResult, setCapsuleResult] = useState(null)
  const [capsulesLoading, setCapsulesLoading] = useState(false)
  const [capsulesError, setCapsulesError] = useState('')

  function hasCompanyAndDomain() {
    return companyName.trim() && domain.trim()
  }

  async function handleGenerateTechnicalPackage() {
    if (!hasCompanyAndDomain()) {
      setTechnicalPackageError('Enter a company name and domain first')
      return
    }
    setTechnicalPackageError('')
    setTechnicalPackageLoading(true)
    try {
      const result = await fetchTechnicalPackage(domain.trim(), companyName.trim())
      setTechnicalPackage(result)
    } catch (error) {
      setTechnicalPackageError(error.message)
      setTechnicalPackage(null)
    } finally {
      setTechnicalPackageLoading(false)
    }
  }

  async function handleGenerateCapsules() {
    if (!hasCompanyAndDomain()) {
      setCapsulesError('Enter a company name and domain first')
      return
    }
    setCapsulesError('')
    setCapsulesLoading(true)
    try {
      const result = await fetchAnswerCapsules(domain.trim(), companyName.trim())
      setCapsuleResult(result)
    } catch (error) {
      setCapsulesError(error.message)
      setCapsuleResult(null)
    } finally {
      setCapsulesLoading(false)
    }
  }

  return (
    <main style={{ maxWidth: 720, margin: '0 auto', padding: '32px 16px', fontFamily: 'sans-serif' }}>
      <h1>geora agent dashboard</h1>
      <p>P0: technical layer package and Answer Capsule generation.</p>

      <section style={{ marginTop: 24 }}>
        <label style={{ display: 'block', marginBottom: 8 }}>
          Company name
          <input
            style={{ display: 'block', width: '100%', padding: 8, marginTop: 4 }}
            value={companyName}
            onChange={(event) => setCompanyName(event.target.value)}
            placeholder="e.g. Acme Inc."
          />
        </label>
        <label style={{ display: 'block' }}>
          Domain
          <input
            style={{ display: 'block', width: '100%', padding: 8, marginTop: 4 }}
            value={domain}
            onChange={(event) => setDomain(event.target.value)}
            placeholder="e.g. acme.com"
          />
        </label>
      </section>

      <section style={{ marginTop: 32, borderTop: '1px solid #ddd', paddingTop: 24 }}>
        <h2>Technical layer package</h2>
        <p>JSON-LD, llms.txt, and robots.txt suggestions. Pure rule-based, no AI call, generates in under a second.</p>
        <button onClick={handleGenerateTechnicalPackage} disabled={technicalPackageLoading}>
          {technicalPackageLoading ? 'Generating…' : 'Generate technical package'}
        </button>
        {technicalPackageError && <p style={{ color: '#b3261e' }}>{technicalPackageError}</p>}

        {technicalPackage && (
          <div style={{ marginTop: 16 }}>
            <p style={{ color: '#666', fontSize: '0.85rem' }}>Cached at {technicalPackage.cached_at}</p>

            <h3>JSON-LD</h3>
            {technicalPackage.json_ld?.status === 'insufficient_data' ? (
              <p>{technicalPackage.json_ld.reason}</p>
            ) : (
              <>
                <pre style={{ background: '#f4f4f4', padding: 12, overflowX: 'auto' }}>
                  {JSON.stringify(technicalPackage.json_ld.organization, null, 2)}
                </pre>
                {technicalPackage.json_ld?.software_application ? (
                  <pre style={{ background: '#f4f4f4', padding: 12, overflowX: 'auto' }}>
                    {JSON.stringify(technicalPackage.json_ld.software_application, null, 2)}
                  </pre>
                ) : (
                  <p style={{ color: '#666', fontSize: '0.85rem' }}>{technicalPackage.json_ld?.note}</p>
                )}
              </>
            )}

            <h3>llms.txt</h3>
            {technicalPackage.llms_txt?.status === 'insufficient_data' ? (
              <p>{technicalPackage.llms_txt.reason}</p>
            ) : (
              <pre style={{ background: '#f4f4f4', padding: 12, whiteSpace: 'pre-wrap' }}>
                {technicalPackage.llms_txt?.content}
              </pre>
            )}

            <h3>robots.txt</h3>
            {technicalPackage.robots_txt?.status === 'insufficient_data' ? (
              <p>{technicalPackage.robots_txt.reason}</p>
            ) : (
              <>
                <p>
                  Already allowed: {technicalPackage.robots_txt.already_allowed.join(', ') || 'none'}
                  <br />
                  Currently blocked or unspecified: {technicalPackage.robots_txt.currently_blocked_or_unspecified.join(', ') || 'none'}
                </p>
                {technicalPackage.robots_txt.suggested_additions && (
                  <pre style={{ background: '#f4f4f4', padding: 12, whiteSpace: 'pre-wrap' }}>
                    {technicalPackage.robots_txt.suggested_additions}
                  </pre>
                )}
              </>
            )}
          </div>
        )}
      </section>

      <section style={{ marginTop: 32, borderTop: '1px solid #ddd', paddingTop: 24 }}>
        <h2>Answer Capsules</h2>
        <p>
          Self-contained answers to real customer questions, written from your own page content. Calls an AI model
          per question, so this can take several seconds to about a minute.
        </p>
        <button onClick={handleGenerateCapsules} disabled={capsulesLoading}>
          {capsulesLoading ? 'Generating…' : 'Generate Answer Capsules'}
        </button>
        {capsulesError && <p style={{ color: '#b3261e' }}>{capsulesError}</p>}

        {capsuleResult && (
          <div style={{ marginTop: 16 }}>
            <p style={{ color: '#666', fontSize: '0.85rem' }}>Cached at {capsuleResult.cached_at}</p>

            {capsuleResult.status !== 'ok' && <p>{capsuleResult.reason}</p>}

            {capsuleResult.capsules?.map((capsule, index) => (
              <div key={index} style={{ borderTop: '1px solid #eee', paddingTop: 12, marginTop: 12 }}>
                <p>
                  <strong>Q:</strong> {capsule.question}
                </p>
                {capsule.existing_answer_found && <p style={{ color: '#2f7d3c' }}>Already answered on the site.</p>}
                {capsule.generation_failed && <p style={{ color: '#b3261e' }}>Generation failed: {capsule.reason}</p>}
                {!capsule.existing_answer_found && !capsule.generation_failed && !capsule.capsule_text && (
                  <p style={{ color: '#666' }}>{capsule.reason}</p>
                )}
                {capsule.capsule_text && (
                  <>
                    <p>{capsule.capsule_text}</p>
                    <p style={{ color: '#666', fontSize: '0.85rem' }}>{capsule.placement_hint}</p>
                  </>
                )}
              </div>
            ))}
          </div>
        )}
      </section>
    </main>
  )
}
