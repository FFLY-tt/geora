// Backend base URL. Hardcoded for now; swap for an env var
// (e.g. import.meta.env.VITE_API_BASE_URL) once a real deployment target exists.
const API_BASE_URL = 'http://localhost:8000'

/**
 * Run the diagnosis for a company/domain against the backend.
 * Throws an Error with a clear message on network failure or non-200 response.
 */
export async function runDiagnosis(companyName, domain, competitors = '') {
  let response
  try {
    response = await fetch(`${API_BASE_URL}/api/diagnose`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        company_name: companyName,
        domain,
        ...(competitors ? { competitors } : {}),
      }),
    })
  } catch (error) {
    console.error('Diagnosis request could not be sent:', error)
    throw new Error("We couldn't reach our servers. Check your internet connection and try again.")
  }

  if (!response.ok) {
    const errorBody = await response.text()
    console.error(`Diagnosis request failed (${response.status}):`, errorBody)
    // The one input problem the backend explains in plain words (e.g. too many competitors).
    if (response.status === 422) {
      let detail
      try {
        detail = JSON.parse(errorBody).detail
      } catch {
        detail = undefined
      }
      if (typeof detail === 'string') throw new Error(detail)
    }
    throw new Error('Something went wrong while running the check. Please try again in a few minutes.')
  }

  return response.json()
}

/**
 * Capture a lead's email together with the diagnosis dimensions they were
 * viewing when they submitted it.
 * Throws an Error with a clear message on network failure or non-200 response.
 */
export async function captureLead(email, companyName, domain, dimensions) {
  let response
  try {
    response = await fetch(`${API_BASE_URL}/api/capture-lead`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email,
        company_name: companyName,
        domain,
        dimensions,
      }),
    })
  } catch (error) {
    console.error('Lead capture request could not be sent:', error)
    throw new Error("We couldn't save your email. Check your internet connection and try again.")
  }

  if (!response.ok) {
    const errorBody = await response.text()
    console.error(`Lead capture request failed (${response.status}):`, errorBody)
    throw new Error("We couldn't save your email. Please try again in a few minutes.")
  }

  return response.json()
}
