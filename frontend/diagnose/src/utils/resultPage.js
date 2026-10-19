// The words at the top of the result page: the headline, the likely range and the one sentence
// that says where the company is missing most, with the pages behind it. Everything is built
// from numbers and pages the backend already sent.
import { RADAR_LABELS } from './radar.js'
import { cleanPageTitle, siteName } from './text.js'
import { pageHref, shortUrl } from './sourceLayers.js'

const GAP_PAGES_SHOWN = 3

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

/** The most used pages of a kind of website that do not mention the company, at most three. */
export function pagesWithoutYou(sourceLayers, layerKey) {
  const layer = (sourceLayers?.layers || []).find((entry) => entry.layer === layerKey)
  return (layer?.pages || [])
    .filter((page) => page.status === 'checked' && !page.mentions_you)
    .sort((a, b) => b.citations - a.citations)
    .slice(0, GAP_PAGES_SHOWN)
    .map((page) => ({
      key: page.url,
      title: cleanPageTitle(page.title) || shortUrl(page.url),
      site: siteName(page.url),
      href: pageHref(page.url),
    }))
}

/**
 * The one-sentence conclusion and the pages under it: { sentence, pages }. Points at the
 * corner of the chart with the biggest difference between gray and blue (radar.biggest_gap).
 */
export function gapConclusion(radar, sourceLayers) {
  if (!radar) {
    return { sentence: 'AI did not use any websites as sources for these questions, so there is nothing to compare.', pages: [] }
  }
  const key = radar.biggest_gap
  if (key) {
    const pages = pagesWithoutYou(sourceLayers, key)
    const where = (RADAR_LABELS[key] || key).toLowerCase()
    const sentence =
      pages.length > 0
        ? `You are missing most from ${where}. These are the pages AI reads there that don't mention you:`
        : `You are missing most from ${where}.`
    return { sentence, pages }
  }
  const none = radar.axes.every((axis) => axis.status !== 'ok' || axis.gray === 0)
  const unreadable = radar.axes.some((axis) => axis.status === 'could_not_check')
  if (none && unreadable) {
    return { sentence: 'We could not open the pages AI used, so we could not check where you are missing.', pages: [] }
  }
  return { sentence: 'Good news. The websites AI reads mostly mention you.', pages: [] }
}
