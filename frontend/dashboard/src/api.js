// Backend base URL for agent_service (a different port/process from
// diagnose_service). Hardcoded for now; swap for an env var
// (e.g. import.meta.env.VITE_AGENT_API_BASE_URL) once a real deployment
// target exists.
const API_BASE_URL = 'http://localhost:8001'

/**
 * Generate the technical layer package (JSON-LD, llms.txt, robots.txt
 * suggestions) for a domain. Pure rule-based on the backend, so this is
 * expected to resolve quickly.
 * Throws an Error with a clear message on network failure or non-200 response.
 */
export async function fetchTechnicalPackage(domain, companyName) {
  let response
  try {
    response = await fetch(`${API_BASE_URL}/api/technical-package`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ domain, company_name: companyName }),
    })
  } catch (error) {
    throw new Error(`Could not reach the agent service: ${error.message}`)
  }

  if (!response.ok) {
    const errorBody = await response.text()
    throw new Error(`Technical package request failed (${response.status}): ${errorBody}`)
  }

  return response.json()
}

/**
 * Generate Answer Capsules for a domain. Calls an LLM per question on
 * the backend, so this can take several seconds to tens of seconds —
 * deliberately a separate, slower request from fetchTechnicalPackage so
 * the UI can show each its own loading state.
 * `questions` is optional: pass brand-free questions already generated
 * elsewhere (e.g. a prior diagnosis run) to skip regenerating them.
 * Throws an Error with a clear message on network failure or non-200 response.
 */
export async function fetchAnswerCapsules(domain, companyName, questions) {
  let response
  try {
    response = await fetch(`${API_BASE_URL}/api/answer-capsules`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        domain,
        company_name: companyName,
        questions: questions && questions.length ? questions : null,
      }),
    })
  } catch (error) {
    throw new Error(`Could not reach the agent service: ${error.message}`)
  }

  if (!response.ok) {
    const errorBody = await response.text()
    throw new Error(`Answer Capsule request failed (${response.status}): ${errorBody}`)
  }

  return response.json()
}
