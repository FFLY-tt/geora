import { test } from 'node:test'
import assert from 'node:assert/strict'
import { fetchFailureText, scoreText, unavailableText } from './scoreDisplay.js'

// Words that must never reach the customer-facing page.
const INTERNAL = /HTTP|\b\d{3}\b|ConnectTimeout|blocked:|unreachable:|PERPLEXITY|DEEPSEEK|DeepSeek|Perplexity|API_KEY|cache|engine|judgment|method version/i

test('a real score is shown as the number', () => {
  assert.equal(scoreText({ score: 75, details: {} }), '75')
  assert.equal(scoreText({ score: 0, details: {} }), '0')
})

test('a blocked homepage shows "Couldn\'t read this website", not a score', () => {
  assert.equal(scoreText({ score: -1, details: { fetch_failure: 'blocked: HTTP 403' } }), "Couldn't read this website")
})

test('other missing data stays "Not enough data"', () => {
  assert.equal(scoreText({ score: -1, details: { status: 'insufficient_data' } }), 'Not enough data')
})

test('fetch failures become plain sentences without status codes', () => {
  for (const failure of ['blocked: HTTP 403', 'http error: HTTP 404', 'unreachable: ConnectTimeout']) {
    const text = fetchFailureText(failure)
    assert.match(text, /couldn't read this website/i, failure)
    assert.doesNotMatch(text, INTERNAL, failure)
  }
  assert.equal(fetchFailureText(undefined), null)
})

test('an unavailable check never shows the technical reason', () => {
  const dimension = {
    score: -1,
    details: {
      reason: 'PERPLEXITY_API_KEY is not configured; no query was performed.',
      plain_finding: { what_we_found: 'We could not test how AI search tools talk about this brand yet.' },
    },
  }
  assert.equal(unavailableText(dimension), 'We could not test how AI search tools talk about this brand yet.')
  assert.doesNotMatch(unavailableText({ score: -1, details: { reason: 'DeepSeek step failed: 500' } }), INTERNAL)
  assert.doesNotMatch(unavailableText({ score: -1, details: { fetch_failure: 'blocked: HTTP 403' } }), INTERNAL)
})
