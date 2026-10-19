import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  citedText,
  dimensionsForLayer,
  formatPercent,
  layerRows,
  pageStatusText,
  pageTitle,
  shortUrl,
  unplacedDimensions,
} from './sourceLayers.js'

const dims = ['citation_visibility', 'topic_relevance', 'evidence_density', 'content_recency', 'fact_consistency', 'technical_readability'].map(
  (dimension) => ({ dimension, score: 50 }),
)

test('percentages', () => {
  assert.equal(formatPercent(0.4), '40%')
  assert.equal(formatPercent(0), '0%')
  assert.equal(formatPercent(null), 'Not checked')
  assert.equal(formatPercent(undefined), 'Not checked')
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
  assert.equal(pageStatusText('guides', { status: 'not_checked', mentions_you: null }), 'Not checked')
})

test('a page is shown by its title, or by its address when there is none', () => {
  assert.equal(pageTitle({ url: 'g2.com/a', title: '  Best CRMs  ' }), 'Best CRMs')
  assert.equal(pageTitle({ url: 'g2.com/a', title: 'Best CRMs 2026 | G2' }), 'Best CRMs 2026')
  assert.equal(pageTitle({ url: 'g2.com/a', title: '(2026)' }), 'g2.com/a')
  assert.equal(pageTitle({ url: 'g2.com/a', title: null }), 'g2.com/a')
  assert.equal(pageTitle({ url: 'g2.com/a', title: '   ' }), 'g2.com/a')
})

test('the three page types of other companies have their own customer wording', () => {
  const rows = layerRows({
    layers: ['lists', 'guides', 'product_pages'].map((layer) => ({ layer, citations: 1, reliance: 0.3, presence: 0, gap: 0.3, status: 'gap' })),
  })
  assert.deepEqual(rows.map((r) => r.label), ['Best tools lists', 'Guides and articles', 'Competitor product pages'])
  for (const row of rows) assert.ok(row.hint.length > 10, row.layer)
})

test('layer rows carry customer wording and no internal terms', () => {
  const rows = layerRows({
    layers: [
      { layer: 'lists', citations: 9, reliance: 0.6, presence: 0, gap: 0.6, status: 'biggest_gap' },
      { layer: 'reference', citations: 0, reliance: 0, presence: null, gap: 0, status: 'not_used' },
      { layer: 'community', citations: 2, reliance: 0.1, presence: null, gap: null, status: 'unknown' },
    ],
  })
  assert.deepEqual(rows.map((r) => [r.label, r.relianceText, r.presenceText, r.statusText]), [
    ['Best tools lists', '60%', '0%', 'Missing most'],
    ['Reference sites', '0%', 'Not used', "AI didn't use this"],
    ['Community discussions', '10%', 'Not checked', "Couldn't check"],
  ])
  for (const row of rows) {
    assert.doesNotMatch(
      `${row.label} ${row.hint} ${row.statusText} ${row.relianceText} ${row.presenceText}`,
      /version|cache|engine|layer|gap|reliance|presence|unknown|null|undefined|[()[\]{}—–|]/i,
    )
  }
})

test('the table says "Missing most" for the same corner as the chart', () => {
  const layers = [
    { layer: 'product_pages', citations: 9, reliance: 0.3, presence: 0, status: 'biggest_gap' },
    { layer: 'lists', citations: 9, reliance: 0.3, presence: 0.2, status: 'gap' },
    { layer: 'guides', citations: 4, reliance: 0.1, presence: 1, status: 'covered' },
  ]
  const rows = layerRows({ layers }, { biggest_gap: 'lists' })
  assert.deepEqual(rows.map((r) => [r.layer, r.statusText]), [
    ['product_pages', 'Missing'],
    ['lists', 'Missing most'],
    ['guides', 'Covered'],
  ])
  // without a chart the table keeps what the backend said
  assert.equal(layerRows({ layers }, null)[0].statusText, 'Missing most')
})

test('url and citation helpers', () => {
  assert.equal(shortUrl('https://g2.com/a'), 'g2.com/a')
  assert.ok(shortUrl(`g2.com/${'x'.repeat(100)}`).endsWith('...'))
  assert.equal(citedText(1), 'used in 1 answer')
  assert.equal(citedText(3), 'used in 3 answers')
})
