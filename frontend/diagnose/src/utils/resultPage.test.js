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

const bars = (mostMissing, rows) => ({ most_missing: mostMissing, max_pages: 10, rows })
const row = (key, name, pages, mention = 0) => ({ key, name, pages, mention_pages: mention, could_not_open: 0, not_checked: 0 })

test('the conclusion names the kind of page with the most pages that do not mention you', () => {
  const { sentence, pages } = gapConclusion(bars('lists', [row('lists', 'Best tools lists', 10, 2)]), sourceLayers)
  assert.equal(sentence, "You are missing most from best tools lists. These are the pages AI read there that don't mention you:")
  assert.equal(pages.length, 3)
})

test('the sentence for each kind of page uses its display name', () => {
  const names = { lists: 'Best tools lists', guides: 'Guides and articles', review_media: 'Review sites and news', community: 'Community discussions' }
  for (const [key, name] of Object.entries(names)) {
    const { sentence } = gapConclusion(bars(key, [row(key, name, 5)]), { layers: [{ layer: key, pages: [] }] })
    assert.equal(sentence, `You are missing most from ${name.toLowerCase()}.`)
  }
})

test('with nothing missing it says you show up everywhere, and says when nothing could be opened', () => {
  const fine = bars(null, [row('lists', 'Best tools lists', 5, 5), row('own_site', 'Your own website', 2, 2)])
  assert.equal(gapConclusion(fine, sourceLayers).sentence, 'You show up on every kind of page AI read. Keep it that way.')
  const unreadable = bars(null, [{ ...row('community', 'Community discussions', 0), could_not_open: 4 }])
  assert.equal(
    gapConclusion(unreadable, sourceLayers).sentence,
    'We could not open the pages AI read, so we could not check where you are missing.',
  )
  assert.equal(
    gapConclusion(null, null).sentence,
    'AI did not use any websites as sources for these questions, so there is nothing to compare.',
  )
})

test('a kind with no page to name gives the sentence without a colon', () => {
  const { sentence, pages } = gapConclusion(bars('guides', [row('guides', 'Guides and articles', 3)]), { layers: [{ layer: 'guides', pages: [] }] })
  assert.equal(sentence, 'You are missing most from guides and articles.')
  assert.deepEqual(pages, [])
})
