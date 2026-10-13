import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  citedText,
  dimensionsForLayer,
  formatPercent,
  layerRows,
  pageStatusText,
  shortUrl,
  unplacedDimensions,
} from './sourceLayers.js'

const dims = ['citation_visibility', 'topic_relevance', 'evidence_density', 'content_recency', 'fact_consistency', 'technical_readability'].map(
  (dimension) => ({ dimension, score: 50 }),
)

test('percentages', () => {
  assert.equal(formatPercent(0.4), '40%')
  assert.equal(formatPercent(0), '0%')
  assert.equal(formatPercent(null), '—')
  assert.equal(formatPercent(undefined), '—')
})

test('fact_consistency sits under review media, the other four under your website', () => {
  assert.deepEqual(dimensionsForLayer('review_media', dims).map((d) => d.dimension), ['fact_consistency'])
  assert.deepEqual(dimensionsForLayer('own_site', dims).map((d) => d.dimension), [
    'topic_relevance',
    'evidence_density',
    'content_recency',
    'technical_readability',
  ])
  assert.deepEqual(dimensionsForLayer('community', dims), [])
})

test('every dimension except the outcome card is placed under a layer', () => {
  assert.deepEqual(unplacedDimensions(dims), [])
  assert.deepEqual(unplacedDimensions([...dims, { dimension: 'something_new', score: 1 }]).map((d) => d.dimension), ['something_new'])
})

test('page status wording', () => {
  assert.equal(pageStatusText('own_site', { status: 'cited', mentions_you: true }), 'Your site')
  assert.equal(pageStatusText('community', { status: 'checked', mentions_you: true }), 'Mentions you')
  assert.equal(pageStatusText('community', { status: 'checked', mentions_you: false }), "Doesn't mention you")
  assert.equal(pageStatusText('community', { status: 'unknown', mentions_you: null }), "Couldn't read this page")
})

test('layer rows carry customer wording and no internal terms', () => {
  const rows = layerRows({
    layers: [
      { layer: 'other_companies', citations: 9, reliance: 0.6, presence: 0, gap: 0.6, status: 'biggest_gap' },
      { layer: 'reference', citations: 0, reliance: 0, presence: null, gap: 0, status: 'not_used' },
      { layer: 'community', citations: 2, reliance: 0.1, presence: null, gap: null, status: 'unknown' },
    ],
  })
  assert.deepEqual(rows.map((r) => [r.label, r.relianceText, r.presenceText, r.statusText]), [
    ["Other companies' websites", '60%', '0%', 'Biggest gap'],
    ['Reference sources', '0%', '—', "AI didn't use this"],
    ['Community sites', '10%', '—', "Couldn't check"],
  ])
  for (const row of rows) {
    assert.doesNotMatch(`${row.label} ${row.hint} ${row.statusText}`, /version|cache|engine|layer_|gap_fraction|null|undefined/i)
  }
})

test('url and citation helpers', () => {
  assert.equal(shortUrl('https://g2.com/a'), 'g2.com/a')
  assert.ok(shortUrl(`g2.com/${'x'.repeat(100)}`).endsWith('...'))
  assert.equal(citedText(1), 'cited in 1 answer')
  assert.equal(citedText(3), 'cited in 3 answers')
})
