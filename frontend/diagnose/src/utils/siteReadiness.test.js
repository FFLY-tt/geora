import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readinessRows, totalText } from './siteReadiness.js'

const item = (key, name, score, level) => ({ key, name, score, level })
const readiness = {
  total: 60,
  level: 'Needs work',
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
  assert.equal(totalText(readiness), '60 out of 100')
  assert.equal(totalText({ ...readiness, total: 0 }), '0 out of 100')
})

test('each row has a name, a bar as long as the score, the score and the level as a word', () => {
  assert.deepEqual(readinessRows(readiness).map((r) => [r.name, r.fill, r.score, r.level]), [
    ['Answers customer questions', 85, '85', 'Good'],
    ['Has real numbers and proof', 55, '55', 'Needs work'],
    ['Up to date', 20, '20', 'Weak'],
  ])
})

test('a check with no result has no row, and with no result at all there is no block', () => {
  assert.equal(readinessRows(readiness).length, 3)
  const none = { ...readiness, total: null, items: readiness.items.map((i) => ({ ...i, score: null, level: null })) }
  assert.deepEqual(readinessRows(none), [])
  assert.equal(totalText(none), null)
  assert.equal(totalText(null), null)
  assert.deepEqual(readinessRows(null), [])
})

test('the bar never goes past the track', () => {
  assert.equal(readinessRows({ items: [item('x', 'X', 140, 'Good')] })[0].fill, 100)
})

test('the rows carry no sentence', () => {
  const [first] = readinessRows(readiness)
  assert.deepEqual(Object.keys(first).sort(), ['fill', 'key', 'level', 'name', 'score'])
})

test('the same scores give the same rows', () => {
  assert.deepEqual(readinessRows(readiness), readinessRows(structuredClone(readiness)))
})
