import { test } from 'node:test'
import assert from 'node:assert/strict'
import { headlineText, mentionCounts, rangeText } from './resultPage.js'

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
