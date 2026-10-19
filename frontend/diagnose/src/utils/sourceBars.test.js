import { test } from 'node:test'
import assert from 'node:assert/strict'
import { barNotes, barRows, barSegments, barText, sourceBarsIntro } from './sourceBars.js'

const row = (over) => ({ key: 'lists', name: 'Best tools lists', pages: 18, mention_pages: 0, could_not_open: 0, not_checked: 0, ...over })

test('the sentence for none, one, several and all pages that mention you', () => {
  assert.equal(barText(row({ pages: 14, mention_pages: 0 })), 'None of these 14 pages mention you')
  assert.equal(barText(row({ pages: 18, mention_pages: 1 })), '1 of 18 pages mentions you')
  assert.equal(barText(row({ pages: 18, mention_pages: 3 })), '3 of 18 pages mention you')
  assert.equal(barText(row({ pages: 5, mention_pages: 5 })), 'All 5 pages mention you')
})

test('a kind with a single page reads as one page', () => {
  assert.equal(barText(row({ pages: 1, mention_pages: 1 })), 'This page mentions you.')
  assert.equal(barText(row({ pages: 1, mention_pages: 0 })), 'This page does not mention you.')
})

test('your own website is all blue and says how many pages AI read', () => {
  assert.equal(barText(row({ key: 'own_site', pages: 3, mention_pages: 3 })), 'AI read 3 pages from your website.')
  assert.equal(barText(row({ key: 'own_site', pages: 1, mention_pages: 1 })), 'AI read 1 page from your website.')
  assert.deepEqual(barSegments(row({ key: 'own_site', pages: 3, mention_pages: 3 }), 6), { total: 50, blue: 50, gray: 0 })
})

test('pages that could not be opened or were not tried get a small line, and a kind with none opened has no bar', () => {
  assert.deepEqual(barNotes(row({ could_not_open: 3 })), ['We could not open 3 more pages.'])
  assert.deepEqual(barNotes(row({ could_not_open: 1, not_checked: 11 })), ['We could not open 1 more page.', 'We did not check 11 more pages.'])
  assert.deepEqual(barNotes(row({ not_checked: 2 })), ['We did not check 2 more pages.'])
  assert.deepEqual(barNotes(row()), [])

  const none = row({ pages: 0, could_not_open: 4 })
  assert.equal(barText(none), 'We could not open these pages.')
  assert.deepEqual(barNotes(none), [])
  assert.deepEqual(barSegments(none, 10), { total: 0, blue: 0, gray: 0 })
  assert.equal(barText(row({ pages: 0, not_checked: 6 })), 'We did not check these pages.')
  assert.deepEqual(barNotes(row({ pages: 0, could_not_open: 2, not_checked: 6 })), ['We did not check 6 more pages.'])
})

test('the longest bar fills the track, blue is the pages that mention you and gray the rest', () => {
  assert.deepEqual(barSegments(row({ pages: 20, mention_pages: 5 }), 20), { total: 100, blue: 25, gray: 75 })
  assert.deepEqual(barSegments(row({ pages: 10, mention_pages: 0 }), 20), { total: 50, blue: 0, gray: 50 })
  assert.deepEqual(barSegments(row({ pages: 10, mention_pages: 10 }), 20), { total: 50, blue: 50, gray: 0 })
})

test('rows come in the order the backend sent them, with text, notes and sizes', () => {
  const rows = barRows({
    max_pages: 20,
    rows: [row({ pages: 20, mention_pages: 5, could_not_open: 1 }), row({ key: 'guides', name: 'Guides and articles', pages: 10, mention_pages: 0 })],
  })
  assert.deepEqual(rows.map((r) => [r.name, r.text, r.notes, r.segments.total]), [
    ['Best tools lists', '5 of 20 pages mention you', ['We could not open 1 more page.'], 100],
    ['Guides and articles', 'None of these 10 pages mention you', [], 50],
  ])
  assert.deepEqual(barRows(null), [])
})

test('no text uses the characters or words the page rules forbid', () => {
  const rows = [row({ pages: 14 }), row({ pages: 18, mention_pages: 1, could_not_open: 3, not_checked: 2 }), row({ pages: 5, mention_pages: 5 }), row({ pages: 0, could_not_open: 3 })]
  for (const r of rows) {
    const text = [barText(r), ...barNotes(r), sourceBarsIntro(15)].join(' ')
    assert.doesNotMatch(text, /[()[\]{}—–|]|\b(gap|presence|reliance|citation|layer|unknown)\b/i)
  }
  assert.equal(sourceBarsIntro(15), 'We asked AI 15 questions your customers ask. To answer them, AI read these kinds of pages.')
})

test('same counts in, same rows out', () => {
  const bars = { max_pages: 9, rows: [row({ pages: 9, mention_pages: 2 })] }
  assert.deepEqual(barRows(bars), barRows(structuredClone(bars)))
})
