import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readinessRows, totalText } from './siteReadiness.js'

const item = (key, name, score, level) => ({ key, name, score, level })
const readiness = {
  total: 70,
  level: 'Good',
  sentence: 'Start by fixing these:',
  fix_first: ['Up to date'],
  items: [
    item('topic_relevance', 'Answers customer questions', 85, 'Good'),
    item('evidence_density', 'Has real numbers and proof', 55, 'Needs work'),
    item('content_recency', 'Up to date', 20, 'Weak'),
    item('technical_readability', 'Easy for AI to read', null, null),
  ],
}

test('the total is written as a score out of 100', () => {
  assert.equal(totalText(readiness), '70 out of 100')
  assert.equal(totalText({ ...readiness, total: 0 }), '0 out of 100')
  assert.equal(totalText({ ...readiness, total: null }), null)
  assert.equal(totalText(null), null)
})

test('each row has a name, a bar size and the level written as a word', () => {
  assert.deepEqual(readinessRows(readiness).map((r) => [r.name, r.fill, r.text]), [
    ['Answers customer questions', 85, '85 Good'],
    ['Has real numbers and proof', 55, '55 Needs work'],
    ['Up to date', 20, '20 Weak'],
    ['Easy for AI to read', 0, 'Not checked'],
  ])
})

test('a check with no result has no bar', () => {
  const [, , , missing] = readinessRows(readiness)
  assert.equal(missing.hasScore, false)
})

test('the bar never goes past the track', () => {
  assert.equal(readinessRows({ items: [item('x', 'X', 140, 'Good')] })[0].fill, 100)
  assert.equal(readinessRows(null).length, 0)
})

test('the same scores give the same rows', () => {
  assert.deepEqual(readinessRows(readiness), readinessRows(structuredClone(readiness)))
})

test('no text uses the characters or words the page rules forbid', () => {
  const text = readinessRows(readiness).map((r) => `${r.name} ${r.text}`).join(' ')
  assert.doesNotMatch(text, /[()[\]{}—–|]|\b(gap|presence|reliance|citation|layer|unknown)\b/i)
})
