import { test } from 'node:test'
import assert from 'node:assert/strict'
import { gapConclusion, headlineText, mentionCounts, pagesWithoutYou, rangeText } from './resultPage.js'

test('the headline counts the answers that mention you', () => {
  assert.equal(headlineText({ mentions: 5, total: 15 }), 'AI mentioned you in 5 of 15 answers.')
  assert.equal(headlineText({ mentions: 0, total: 15 }), 'AI mentioned you in 0 of 15 answers.')
})

test('an older result without counts is added up from the answers', () => {
  const details = { per_query_results: [{ runs: [{ status: 'ok', is_hit: true }, { status: 'error' }] }, { runs: [{ status: 'ok', is_hit: false }] }] }
  assert.deepEqual(mentionCounts(details), { mentions: 1, total: 2 })
  assert.equal(rangeText(details), null)
})

test('the range is written in plain words with no dash', () => {
  assert.equal(rangeText({ mentions: 5, total: 15, ci_low: 0.15, ci_high: 0.58 }), 'The true rate is likely between 15% and 58%.')
  assert.equal(rangeText({ mentions: 0, total: 15, ci_low: 0, ci_high: 0.204 }), 'The true rate is likely between 0% and 20%.')
  assert.equal(rangeText({ mentions: 5, total: 15 }), null)
})

const sourceLayers = {
  layers: [
    {
      layer: 'lists',
      pages: [
        { url: 'a.com/one', title: 'Top Tools 2026 | A Site', citations: 2, status: 'checked', mentions_you: false },
        { url: 'b.com/two', title: 'Best Tools (updated)', citations: 5, status: 'checked', mentions_you: false },
        { url: 'c.com/three', title: 'Mentions You', citations: 9, status: 'checked', mentions_you: true },
        { url: 'd.com/four', title: 'Could not open', citations: 8, status: 'unknown', mentions_you: null },
        { url: 'e.com/five', title: null, citations: 1, status: 'checked', mentions_you: false },
        { url: 'f.com/six', title: 'Sixth', citations: 1, status: 'checked', mentions_you: false },
      ],
    },
  ],
}

test('the pages under the conclusion are the most used ones that do not mention you, at most three, cleaned', () => {
  assert.deepEqual(pagesWithoutYou(sourceLayers, 'lists'), [
    { key: 'b.com/two', title: 'Best Tools', site: 'b.com', href: 'https://b.com/two' },
    { key: 'a.com/one', title: 'Top Tools 2026', site: 'a.com', href: 'https://a.com/one' },
    { key: 'e.com/five', title: 'e.com/five', site: 'e.com', href: 'https://e.com/five' },
  ])
})

test('the conclusion points at the corner with the biggest difference', () => {
  const radar = { biggest_gap: 'lists', axes: [{ key: 'lists', gray: 50, blue: 10, status: 'ok' }] }
  const { sentence, pages } = gapConclusion(radar, sourceLayers)
  assert.equal(sentence, "You are missing most from best tools lists. These are the pages AI reads there that don't mention you:")
  assert.equal(pages.length, 3)
})

test('without a gap the conclusion says so, and says when nothing could be checked', () => {
  const fine = { biggest_gap: null, axes: [{ key: 'lists', gray: 50, blue: 50, status: 'ok' }] }
  assert.equal(gapConclusion(fine, sourceLayers).sentence, 'Good news. The websites AI reads mostly mention you.')
  const unreadable = { biggest_gap: null, axes: [{ key: 'lists', gray: 50, blue: 0, status: 'could_not_check' }] }
  assert.equal(
    gapConclusion(unreadable, sourceLayers).sentence,
    'We could not open the pages AI used, so we could not check where you are missing.',
  )
  assert.equal(
    gapConclusion(null, null).sentence,
    'AI did not use any websites as sources for these questions, so there is nothing to compare.',
  )
})

test('a gap corner with no page to name gives the sentence without a colon', () => {
  const radar = { biggest_gap: 'guides', axes: [{ key: 'guides', gray: 30, blue: 0, status: 'ok' }] }
  const { sentence, pages } = gapConclusion(radar, { layers: [{ layer: 'guides', pages: [] }] })
  assert.equal(sentence, 'You are missing most from guides and articles.')
  assert.deepEqual(pages, [])
})
