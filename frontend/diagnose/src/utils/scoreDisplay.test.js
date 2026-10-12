import { test } from 'node:test'
import assert from 'node:assert/strict'
import { scoreText } from './scoreDisplay.js'

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
