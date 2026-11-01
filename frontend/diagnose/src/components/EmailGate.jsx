import { useState } from 'react'

const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

// Props: onUnlock(email) — called only after the email passes validation.
export default function EmailGate({ onUnlock }) {
  const [email, setEmail] = useState('')
  const [error, setError] = useState('')

  function handleSubmit(event) {
    event.preventDefault()

    if (!EMAIL_REGEX.test(email.trim())) {
      setError('Enter a valid email first')
      return
    }

    setError('')
    onUnlock(email.trim())
  }

  return (
    <form className="email-gate" onSubmit={handleSubmit}>
      <p className="email-gate-title">Enter your email to unlock the details</p>
      <input
        type="email"
        value={email}
        onChange={(event) => setEmail(event.target.value)}
        placeholder="you@company.com"
      />
      <button type="submit" className="btn-mechanical">Unlock details</button>
      {error && <p className="form-error">{error}</p>}
    </form>
  )
}
