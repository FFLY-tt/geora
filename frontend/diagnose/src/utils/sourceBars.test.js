import { test } from 'node:test'
import assert from 'node:assert/strict'
import { barRows, barSegments } from './sourceBars.js'

const row = (over) => ({ key: 'lists', name: 'Best tools lists', pages: 29, mention_pages: 1, could_not_open: 0, not_checked: 0, ...over })

test('the number on the right is the pages that mention you out of the pages we opened', () => {
  const rows = barRows({ rows: [row(), row({ key: 'guides', name: 'Guides and articles', pages: 14, mention_pages: 0 })] })
  assert.deepEqual(rows.map((r) => [r.name, r.text]), [
    ['Best tools lists', '1 of 29'],
    ['Guides and articles', '0 of 14'],
  ])
})

test('your own website is all blue and reads 2 of 2', () => {
  const [own] = barRows({ rows: [row({ key: 'own_site', name: 'Your own website', pages: 2, mention_pages: 2 })] })
  assert.equal(own.text, '2 of 2')
  assert.deepEqual(own.segments, { total: 100, blue: 100, gray: 0 })
})

test('a kind with no opened page has no row, whatever else is known about it', () => {
  const rows = barRows({
    rows: [
      row({ pages: 5 }),
      row({ key: 'community', name: 'Community discussions', pages: 0, could_not_open: 4 }),
      row({ key: 'guides', name: 'Guides and articles', pages: 0, not_checked: 20 }),
      row({ key: 'review_media', name: 'Review sites and news', pages: 0 }),
    ],
  })
  assert.deepEqual(rows.map((r) => r.key), ['lists'])
  assert.deepEqual(barRows({ rows: [row({ pages: 0 })] }), [])
  assert.deepEqual(barRows(null), [])
})

test('rows are sorted by the pages we opened, most first', () => {
  const rows = barRows({
    rows: [row({ key: 'guides', pages: 6 }), row({ key: 'lists', pages: 47 }), row({ key: 'review_media', pages: 16 })],
  })
  assert.deepEqual(rows.map((r) => r.key), ['lists', 'review_media', 'guides'])
})

test('the kind with the most pages fills the track, blue is the pages that mention you and gray the rest', () => {
  const rows = barRows({ rows: [row({ pages: 20, mention_pages: 5 }), row({ key: 'guides', pages: 10, mention_pages: 0 })] })
  assert.deepEqual(rows[0].segments, { total: 100, blue: 25, gray: 75 })
  assert.deepEqual(rows[1].segments, { total: 50, blue: 0, gray: 50 })
  assert.deepEqual(barSegments(row({ pages: 0 }), 10), { total: 0, blue: 0, gray: 0 })
})

test('the rows carry nothing but a name, a number and the sizes', () => {
  const [first] = barRows({ rows: [row({ could_not_open: 3, not_checked: 11 })] })
  assert.deepEqual(Object.keys(first).sort(), ['key', 'name', 'segments', 'text'])
  assert.doesNotMatch(first.text, /could|check|page|[()[\]{}—–|]/i)
})

test('same counts in, same rows out', () => {
  const bars = { rows: [row({ pages: 9, mention_pages: 2 })] }
  assert.deepEqual(barRows(bars), barRows(structuredClone(bars)))
})
