import { useState } from 'react'
import { countCompetitors, MAX_COMPETITORS } from '../utils/competitors.js'

// Controlled form for entering the company name and domain to diagnose.
export default function SearchForm({ onSubmit, loading }) {
  const [companyName, setCompanyName] = useState('')
  const [domain, setDomain] = useState('')
  const [competitors, setCompetitors] = useState('')
  const [error, setError] = useState('')

  function handleSubmit(event) {
    event.preventDefault()

    if (!companyName.trim() || !domain.trim()) {
      setError('Enter a company name and domain first')
      return
    }

    if (countCompetitors(competitors) > MAX_COMPETITORS) {
      setError(`Add at most ${MAX_COMPETITORS} competitors`)
      return
    }

    setError('')
    onSubmit(companyName.trim(), domain.trim(), competitors.trim())
  }

  return (
    <form className="search-form" onSubmit={handleSubmit}>
      <div className="form-field">
        <label htmlFor="company-name">Company name</label>
        <input
          id="company-name"
          type="text"
          value={companyName}
          onChange={(event) => setCompanyName(event.target.value)}
          disabled={loading}
          placeholder="e.g. Acme Inc."
        />
      </div>

      <div className="form-field">
        <label htmlFor="domain">Website</label>
        <input
          id="domain"
          type="text"
          value={domain}
          onChange={(event) => setDomain(event.target.value)}
          disabled={loading}
          placeholder="e.g. acme.com"
        />
        <p className="form-hint">e.g. yourcompany.com — no need for https://</p>
      </div>

      <div className="form-field">
        <label htmlFor="competitors">Competitors (optional)</label>
        <input
          id="competitors"
          type="text"
          value={competitors}
          onChange={(event) => setCompetitors(event.target.value)}
          disabled={loading}
          placeholder="e.g. HubSpot, Pipedrive (pipedrive.com)"
        />
        <p className="form-hint">Up to 5, separated by commas. We'll show how often each one is mentioned too.</p>
      </div>

      {error && <p className="form-error">{error}</p>}

      <button type="submit" className="btn-mechanical" disabled={loading}>
        {loading ? 'Running...' : 'Run diagnosis'}
      </button>
    </form>
  )
}
