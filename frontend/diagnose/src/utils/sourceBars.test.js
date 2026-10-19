import { test } from 'node:test'
import assert from 'node:assert/strict'
import { barRows, barSegments } from './sourceBars.js'

const row = (over) => ({ key: 'lists', name: 'Best tools lists', pages: 29, mention_pages: 1, could_not_open: 0, not_checked: 0, ...over })
const own = (pages) => row({ key: 'own_site', name: 'Your own website', pages, mention_pages: pages })

test('the number on the right is the pages that mention you out of the pages we opened', () => {
  const rows = barRows({ rows: [row(), row({ key: 'guides', name: 'Guides and articles', pages: 14, mention_pages: 0 })] })
  assert.deepEqual(rows.slice(0, 2).map((r) => [r.name, r.text]), [
    ['Best tools lists', '1 of 29'],
    ['Guides and articles', '0 of 14'],
  ])
})

test('when AI read your own website its row is all blue and reads N of N', () => {
  const rows = barRows({ rows: [row({ pages: 4 }), own(2)] })
  const last = rows[rows.length - 1]
  assert.equal(last.text, '2 of 2')
  assert.equal(last.empty, false)
  assert.deepEqual(last.segments, { total: 50, blue: 50, gray: 0 })
  assert.equal(barRows({ rows: [own(3)] })[0].text, '3 of 3')
})

test('when AI never used your own website the row is still there, with an empty bar and just 0', () => {
  const rows = barRows({ rows: [row()] })
  assert.deepEqual(rows.map((r) => r.key), ['lists', 'own_site'])
  const last = rows[1]
  assert.deepEqual([last.name, last.text, last.empty], ['Your own website', '0', true])
  assert.deepEqual(last.segments, { total: 0, blue: 0, gray: 0 })
  // the same when the backend sends a row for it with no pages
  assert.equal(barRows({ rows: [row(), own(0)] })[1].text, '0')
})

test('your own website is always the last row, whatever its pages', () => {
  const big = barRows({ rows: [own(90), row({ pages: 5 }), row({ key: 'guides', pages: 8 })] })
  assert.deepEqual(big.map((r) => r.key), ['guides', 'lists', 'own_site'])
  const small = barRows({ rows: [row({ pages: 5 }), own(1), row({ key: 'guides', pages: 8 })] })
  assert.deepEqual(small.map((r) => r.key), ['guides', 'lists', 'own_site'])
})

test('the other kinds still have no row when they have no opened page', () => {
  const rows = barRows({
    rows: [
      row({ pages: 5 }),
      row({ key: 'community', name: 'Community discussions', pages: 0, could_not_open: 4 }),
      row({ key: 'guides', name: 'Guides and articles', pages: 0, not_checked: 20 }),
      row({ key: 'review_media', name: 'Review sites and news', pages: 0 }),
    ],
  })
  assert.deepEqual(rows.map((r) => r.key), ['lists', 'own_site'])
  assert.deepEqual(barRows({ rows: [row({ pages: 0 })] }).map((r) => r.key), ['own_site'])
})

test('with no source bars at all there is nothing to draw', () => {
  assert.deepEqual(barRows(null), [])
})

test('the other rows are sorted by the pages we opened, most first', () => {
  const rows = barRows({
    rows: [row({ key: 'guides', pages: 6 }), row({ key: 'lists', pages: 47 }), row({ key: 'review_media', pages: 16 })],
  })
  assert.deepEqual(rows.map((r) => r.key), ['lists', 'review_media', 'guides', 'own_site'])
})

test('the kind with the most pages fills the track, blue is the pages that mention you and gray the rest', () => {
  const rows = barRows({ rows: [row({ pages: 20, mention_pages: 5 }), row({ key: 'guides', pages: 10, mention_pages: 0 })] })
  assert.deepEqual(rows[0].segments, { total: 100, blue: 25, gray: 75 })
  assert.deepEqual(rows[1].segments, { total: 50, blue: 0, gray: 50 })
  assert.deepEqual(barSegments(row({ pages: 0 }), 10), { total: 0, blue: 0, gray: 0 })
})

test('the rows carry nothing but a name, a number and the sizes', () => {
  const [first] = barRows({ rows: [row({ could_not_open: 3, not_checked: 11 })] })
  assert.deepEqual(Object.keys(first).sort(), ['empty', 'key', 'name', 'segments', 'text'])
  assert.doesNotMatch(first.text, /could|check|page|[()[\]{}—–|]/i)
})

test('same counts in, same rows out', () => {
  const bars = { rows: [row({ pages: 9, mention_pages: 2 })] }
  assert.deepEqual(barRows(bars), barRows(structuredClone(bars)))
})
