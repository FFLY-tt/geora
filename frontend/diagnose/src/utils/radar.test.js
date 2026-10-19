import { test } from 'node:test'
import assert from 'node:assert/strict'
import { radarChartData, radarCorners, radarSentence, RADAR_LABELS, wrapLines } from './radar.js'

// The numbers as the backend sends them (see backend/diagnose_service/radar.py).
const radar = {
  scale_max: 60,
  biggest_gap: 'lists',
  total_sources: 150,
  axes: [
    { key: 'lists', gray: 58.7, blue: 16.9, status: 'ok' },
    { key: 'guides', gray: 22.7, blue: 0, status: 'ok' },
    { key: 'review_media', gray: 8.7, blue: 2.2, status: 'ok' },
    { key: 'community', gray: 0, blue: 0, status: 'not_used' },
    { key: 'own_site', gray: 1.3, blue: 1.3, status: 'ok' },
  ],
}

test('the corners come in the fixed order with their customer names', () => {
  assert.deepEqual(radarCorners(radar).map((c) => c.label), [
    'Best tools lists',
    'Guides and articles',
    'Review sites and news',
    'Community discussions',
    'Your website',
  ])
  assert.equal(RADAR_LABELS.own_site, 'Your website')
})

test('the chart gets both shapes and one shared scale', () => {
  const data = radarChartData(radar)
  assert.deepEqual(data.gray, [58.7, 22.7, 8.7, 0, 1.3])
  assert.deepEqual(data.blue, [16.9, 0, 2.2, 0, 1.3])
  assert.equal(data.scaleMax, 60)
})

test('blue is never drawn outside gray, and your website is the same in both', () => {
  const data = radarChartData(radar)
  data.blue.forEach((value, index) => assert.ok(value <= data.gray[index], `corner ${index}`))
  assert.equal(data.blue[4], data.gray[4])
})

test('a corner AI never used, and one that could not be checked, carry a small note', () => {
  const corners = radarCorners({
    ...radar,
    axes: [
      { key: 'lists', gray: 40, blue: 10, status: 'ok' },
      { key: 'guides', gray: 12, blue: 0, status: 'could_not_check' },
      { key: 'review_media', gray: 0, blue: 0, status: 'not_used' },
      { key: 'community', gray: 0, blue: 0, status: 'not_used' },
      { key: 'own_site', gray: 0, blue: 0, status: 'not_used' },
    ],
  })
  assert.deepEqual(corners.map((c) => c.note), ['', 'Could not check', 'Not used', 'Not used', 'Not used'])
})

test('the sentence for a corner', () => {
  const axis = (gray, blue, status = 'ok', key = 'guides') => ({ key, gray, blue, status })
  assert.equal(
    radarSentence(axis(55.2, 0)),
    'AI used guides and articles for 55 out of every 100 sources. None of them mention you.',
  )
  assert.equal(
    radarSentence(axis(58.7, 16.9, 'ok', 'lists')),
    'AI used best tools lists for 59 out of every 100 sources. About 17 of those 59 mention you.',
  )
  assert.equal(radarSentence(axis(1.3, 1.3, 'ok', 'own_site')), 'AI used your website for 1 out of every 100 sources. All of them mention you.')
  assert.equal(radarSentence(axis(0, 0, 'not_used', 'community')), 'AI did not use community discussions.')
  assert.equal(
    radarSentence(axis(12, 0, 'could_not_check', 'review_media')),
    'AI used review sites and news for 12 out of every 100 sources. We could not open these pages to check them.',
  )
  assert.equal(radarSentence(axis(0.3, 0)), 'AI used guides and articles for fewer than 1 out of every 100 sources. None of them mention you.')
  assert.equal(
    radarSentence(axis(20, 0.3)),
    'AI used guides and articles for 20 out of every 100 sources. Only a very few of them mention you.',
  )
})

test('no sentence uses the characters or words the page rules forbid', () => {
  for (const corner of radarCorners(radar)) {
    assert.doesNotMatch(`${corner.label} ${corner.note} ${corner.sentence}`, /[()[\]{}—–|]|\b(gap|presence|reliance|citation|layer)\b/i, corner.key)
  }
})

test('the pointer sentence is split into short lines', () => {
  const lines = wrapLines('AI used guides and articles for 55 out of every 100 sources. None of them mention you.', 38)
  assert.ok(lines.length > 1)
  for (const line of lines) assert.ok(line.length <= 38, line)
  assert.equal(lines.join(' '), 'AI used guides and articles for 55 out of every 100 sources. None of them mention you.')
})

test('same numbers in, same corners out', () => {
  assert.deepEqual(radarCorners(radar), radarCorners(structuredClone(radar)))
  assert.deepEqual(radarChartData(radar), radarChartData(structuredClone(radar)))
})

test('no radar gives no corners', () => {
  assert.deepEqual(radarCorners(null), [])
})
