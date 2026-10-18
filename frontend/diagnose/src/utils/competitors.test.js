import { test } from 'node:test'
import assert from 'node:assert/strict'
import { comparisonLines, countCompetitors, MAX_COMPETITORS } from './competitors.js'

test('counts non-blank comma separated entries', () => {
  assert.equal(countCompetitors(''), 0)
  assert.equal(countCompetitors(' , ,'), 0)
  assert.equal(countCompetitors('HubSpot'), 1)
  assert.equal(countCompetitors('HubSpot, Pipedrive (pipedrive.com),Salesforce'), 3)
  assert.equal(countCompetitors('A, B, C, D, E, F') > MAX_COMPETITORS, true)
})

test('no comparison gives no lines', () => {
  assert.deepEqual(comparisonLines(null), [])
  assert.deepEqual(comparisonLines({}), [])
})

test('lines keep the backend order and read "X of N answers"', () => {
  const lines = comparisonLines({
    entries: [
      { name: 'HubSpot', domain: '', is_you: false, mentions: 10, total: 15 },
      { name: 'Close', domain: 'close.com', is_you: true, mentions: 3, total: 15 },
    ],
  })
  assert.deepEqual(
    lines.map((line) => [line.name, line.isYou, line.text]),
    [
      ['HubSpot', false, '10 of 15 answers'],
      ['Close', true, '3 of 15 answers'],
    ],
  )
})
