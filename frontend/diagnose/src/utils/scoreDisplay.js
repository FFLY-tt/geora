// Customer-facing wording for scores and for checks that couldn't run.
// Backend details carry technical reasons (HTTP codes, API errors, method
// versions) for our own analysis; none of that is shown on the page.
// These helpers turn it into plain sentences a customer can act on.

// What to show in place of a dimension's score. -1 is never a real score:
// it means the dimension couldn't be measured. When the site itself
// couldn't be read (details.fetch_failure), say that instead of a generic
// "not enough data".
export function scoreText(dimension) {
  if (dimension.score !== -1) return String(dimension.score)
  if (dimension.details?.fetch_failure) return "Couldn't read this website"
  return 'Not enough data'
}

/**
 * Plain explanation for details.fetch_failure ("blocked: HTTP 403",
 * "http error: HTTP 404", "unreachable: ConnectTimeout"). Never includes
 * the status code or exception name itself.
 */
export function fetchFailureText(fetchFailure) {
  if (!fetchFailure) return null
  if (fetchFailure.startsWith('blocked')) {
    return "We couldn't read this website because it blocked our visit. Some sites turn away automated checks like ours."
  }
  if (fetchFailure.startsWith('unreachable')) {
    return "We couldn't read this website because we couldn't connect to it. It may have been down when we checked."
  }
  return "We couldn't read this website because its homepage returned an error when we opened it."
}

/**
 * The customer-facing sentence for a check that couldn't run: the
 * backend's plain_finding (written for customers) when there is one,
 * never its technical `reason` field.
 */
export function unavailableText(dimension) {
  const failure = fetchFailureText(dimension?.details?.fetch_failure)
  if (failure) return failure
  const finding = dimension?.details?.plain_finding
  if (finding && typeof finding === 'object' && finding.what_we_found?.trim()) return finding.what_we_found
  if (typeof finding === 'string' && finding.trim()) return finding
  return "We couldn't run this check this time. Please try again later."
}
