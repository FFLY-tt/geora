// Run with `npm test` (node's built-in test runner, no extra dependencies).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import * as provenance from './provenance.js'

const { isComparable, shouldShowScoreChange } = provenance

const base = {
  run_date: '2026-10-11',
  method_versions: { citation_visibility: 'v1', topic_relevance: 'v1', evidence_density: 'v1' },
  question_set: { category_id: 'crm', category_name: 'CRM', version: 1, status: 'frozen' },
}

const moved = { evidence_density: 40 }
const current = [{ dimension: 'evidence_density', score: 70 }]

test('same method and question set versions are comparable', () => {
  assert.equal(isComparable(base, structuredClone(base)), true)
})

test('a different method version is not comparable', () => {
  const other = structuredClone(base)
  other.method_versions.evidence_density = 'v2'
  assert.equal(isComparable(base, other), false)
})

test('a different question set version is not comparable', () => {
  const other = structuredClone(base)
  other.question_set.version = 2
  assert.equal(isComparable(base, other), false)
})

test('a previous result without provenance is not comparable', () => {
  assert.equal(isComparable(null, base), false)
  assert.equal(isComparable({}, base), false)
})

test('a real move with matching versions shows the score change note', () => {
  assert.equal(shouldShowScoreChange(moved, current, base, structuredClone(base)), true)
})

test('a different method version shows nothing at all, even when scores moved', () => {
  const other = structuredClone(base)
  other.method_versions.evidence_density = 'v2'
  assert.equal(shouldShowScoreChange(moved, current, base, other), false)
})

test('a first-ever check or a small move shows nothing', () => {
  assert.equal(shouldShowScoreChange(null, current, base, base), false)
  assert.equal(shouldShowScoreChange({ evidence_density: 68 }, current, base, structuredClone(base)), false)
})

test('the version footer text no longer exists', () => {
  assert.equal(provenance.describeVersions, undefined)
})
