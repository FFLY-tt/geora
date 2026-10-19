import { test } from 'node:test'
import assert from 'node:assert/strict'
import { describeChange, intervalsOverlap } from './mentionInterval.js'

test('overlap', () => {
  assert.equal(intervalsOverlap({ ci_low: 0.07, ci_high: 0.45 }, { ci_low: 0.2, ci_high: 0.6 }), true)
  assert.equal(intervalsOverlap({ ci_low: 0.0, ci_high: 0.2 }, { ci_low: 0.5, ci_high: 0.9 }), false)
  assert.equal(intervalsOverlap(null, { ci_low: 0.5, ci_high: 0.9 }), false)
})

const dims = (citation, other = 50) => [
  { dimension: 'citation_visibility', score: citation },
  { dimension: 'evidence_density', score: other },
]

test('overlapping intervals: within the margin of error, not a rise or drop', () => {
  const result = describeChange(
    { citation_visibility: 20, evidence_density: 50 },
    dims(33),
    { ci_low: 0.07, ci_high: 0.45 },
    { ci_low: 0.15, ci_high: 0.58 },
  )
  assert.deepEqual(result, { realChange: false, withinMargin: true })
})

test('non-overlapping intervals: a real change', () => {
  const result = describeChange(
    { citation_visibility: 0, evidence_density: 50 },
    dims(80),
    { ci_low: 0.0, ci_high: 0.2 },
    { ci_low: 0.55, ci_high: 0.93 },
  )
  assert.deepEqual(result, { realChange: true, withinMargin: false })
})

test('no intervals: citation falls back to the point threshold', () => {
  assert.deepEqual(describeChange({ citation_visibility: 20 }, dims(40), null, null), {
    realChange: true,
    withinMargin: false,
  })
})

test('other dimensions keep the threshold rule', () => {
  const result = describeChange(
    { citation_visibility: 20, evidence_density: 50 },
    dims(20, 70),
    { ci_low: 0.07, ci_high: 0.45 },
    { ci_low: 0.07, ci_high: 0.45 },
  )
  assert.deepEqual(result, { realChange: true, withinMargin: false })
})
