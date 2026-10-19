// The words at the top of the result page: the headline and the likely range. Everything is
// built from numbers the backend already sent.

function percent(value) {
  return `${Math.round(value * 100)}%`
}

/** The mentions and answers behind the headline, from the citation dimension's details. */
export function mentionCounts(details) {
  if (details && Number.isFinite(details.mentions) && Number.isFinite(details.total) && details.total > 0) {
    return { mentions: details.mentions, total: details.total }
  }
  let mentions = 0
  let total = 0
  for (const entry of details?.per_query_results || []) {
    for (const run of entry.runs || []) {
      if (run.status === 'ok') {
        total += 1
        if (run.is_hit) mentions += 1
      }
    }
  }
  return { mentions, total }
}

/** "AI mentioned you in 5 of 15 answers." */
export function headlineText(details) {
  const { mentions, total } = mentionCounts(details)
  return `AI mentioned you in ${mentions} of ${total} answers.`
}

/** "The true rate is likely between 15% and 58%." or null when there is no range to give. */
export function rangeText(details) {
  const { total } = mentionCounts(details)
  const low = details?.ci_low
  const high = details?.ci_high
  if (!total || low === null || low === undefined || high === null || high === undefined) return null
  return `The true rate is likely between ${percent(low)} and ${percent(high)}.`
}
