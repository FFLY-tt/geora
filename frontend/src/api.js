// Backend base URL. Hardcoded for now; swap for an env var
// (e.g. import.meta.env.VITE_API_BASE_URL) once a real deployment target exists.
const API_BASE_URL = 'http://localhost:8000'

/**
 * Run the diagnosis for a company/domain against the backend.
 * Throws an Error with a clear message on network failure or non-200 response.
 */
export async function runDiagnosis(companyName, domain) {
  let response
  try {
    response = await fetch(`${API_BASE_URL}/api/diagnose`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        company_name: companyName,
        domain,
      }),
    })
  } catch (error) {
    throw new Error(`Could not reach the diagnosis service: ${error.message}`)
  }

  if (!response.ok) {
    const errorBody = await response.text()
    throw new Error(`Diagnosis request failed (${response.status}): ${errorBody}`)
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
    throw new Error(`Could not reach the lead capture service: ${error.message}`)
  }

  if (!response.ok) {
    const errorBody = await response.text()
    throw new Error(`Lead capture request failed (${response.status}): ${errorBody}`)
  }

  return response.json()
}
