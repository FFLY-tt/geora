import { useState } from 'react'

// Controlled form for entering the company name and domain to diagnose.
export default function SearchForm({ onSubmit, loading }) {
  const [companyName, setCompanyName] = useState('')
  const [domain, setDomain] = useState('')
  const [error, setError] = useState('')

  function handleSubmit(event) {
    event.preventDefault()

    if (!companyName.trim() || !domain.trim()) {
      setError('Enter a company name and domain first')
      return
    }

    setError('')
    onSubmit(companyName.trim(), domain.trim())
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

      {error && <p className="form-error">{error}</p>}

      <button type="submit" className="btn-mechanical" disabled={loading}>
        {loading ? 'Running...' : 'Run diagnosis'}
      </button>
    </form>
  )
}
