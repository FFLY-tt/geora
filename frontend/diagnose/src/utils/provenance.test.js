// Run with `npm test` (node's built-in test runner, no extra dependencies).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { describeVersions, isComparable } from './provenance.js'

const base = {
  run_date: '2026-10-11',
  method_versions: { citation_visibility: 'v1', topic_relevance: 'v1', evidence_density: 'v1' },
  question_set: { category_id: 'crm', category_name: 'CRM', version: 1, status: 'frozen' },
}

test('same method and question set versions are comparable', () => {
  assert.equal(isComparable(base, structuredClone(base)), true)
})

test('a different method version is not comparable, so no score comparison is shown', () => {
  const current = structuredClone(base)
  current.method_versions.evidence_density = 'v2'
  assert.equal(isComparable(base, current), false)
})

test('a different question set version is not comparable', () => {
  const current = structuredClone(base)
  current.question_set.version = 2
  assert.equal(isComparable(base, current), false)
})

test('a previous result without provenance is not comparable', () => {
  assert.equal(isComparable(null, base), false)
  assert.equal(isComparable({}, base), false)
})

test('the footer line names the method and question set versions', () => {
  const line = describeVersions(base)
  assert.match(line, /citation_visibility v1/)
  assert.match(line, /Question set: crm v1/)
})

test('a needs_attention question set is flagged in the footer', () => {
  const current = structuredClone(base)
  current.question_set.status = 'needs_attention'
  assert.match(describeVersions(current), /quality pending review/)
})
