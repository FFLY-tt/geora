import { test } from 'node:test'
import assert from 'node:assert/strict'
import { factRows, quoteSnippet, SNIPPET_LENGTH, verdictText } from './factFields.js'

// Words and details that must never reach the customer-facing page. ("match" is
// allowed inside the sentences "Matches your site" and "Doesn't match your site".)
const INTERNAL = /\b(conflict|unknown)\b|votes?\b|\bruns?\b|majority|voting|\b2 of 3\b/

const details = {
  voting_rule: 'Each field is judged on 3 independent runs. It is a conflict only if at least 2 runs say conflict...',
  runs: [{ run: 1 }],
  fields: [
    {
      field: 'starting_price',
      label: 'Starting price',
      verdict: 'conflict',
      votes: ['conflict', 'conflict', 'match'],
      third_party_value: '$49 per user per month',
      third_party_source: 'https://www.g2.com/products/acme/reviews',
      official_value: '$29 per user per month',
      official_quote: 'Plans start at $29 per user per month.',
    },
    {
      field: 'deployment',
      label: 'Deployment',
      verdict: 'match',
      votes: ['match', 'match', 'unknown'],
      third_party_value: 'Cloud',
      third_party_source: 'https://www.capterra.com/p/1/acme',
      official_value: 'Cloud-based',
      official_quote: 'Cloud-based, no install needed.',
    },
    { field: 'target_customer', label: 'Target customer', verdict: 'unknown', votes: ['unknown', 'unknown', 'match'] },
  ],
}

test('each verdict reads as a plain conclusion', () => {
  assert.equal(verdictText('match'), 'Matches your site')
  assert.equal(verdictText('conflict'), "Doesn't match your site")
  assert.equal(verdictText('unknown'), 'Not enough information')
  assert.equal(verdictText(undefined), 'Not enough information')
  assert.equal(verdictText('something_new'), 'Not enough information')
})

test('rows carry the conclusion, the review sites value with its source, and your sites value with its quote', () => {
  const [price, deployment] = factRows(details)
  assert.deepEqual(price, {
    key: 'starting_price',
    label: 'Starting price',
    verdictText: "Doesn't match your site",
    thirdPartyValue: '$49 per user per month',
    thirdPartySource: 'https://www.g2.com/products/acme/reviews',
    officialValue: '$29 per user per month',
    officialQuote: 'Plans start at $29 per user per month.',
  })
  assert.equal(deployment.verdictText, 'Matches your site')
})

test('a field with no values says so instead of showing nothing', () => {
  const unknown = factRows(details)[2]
  assert.equal(unknown.verdictText, 'Not enough information')
  assert.equal(unknown.thirdPartyValue, 'Not stated')
  assert.equal(unknown.officialValue, 'Not stated')
  assert.equal(unknown.thirdPartySource, null)
  assert.equal(unknown.officialQuote, null)
})

test('the votes, the runs and the voting rule never reach the rows', () => {
  const text = JSON.stringify(factRows(details).map(({ key, ...shown }) => shown))
  assert.doesNotMatch(text, INTERNAL)
  assert.doesNotMatch(text, /voting_rule|"votes"/)
  for (const row of factRows(details)) {
    assert.ok(!['match', 'conflict', 'unknown'].includes(row.verdictText), row.verdictText)
  }
})

test('missing details give no rows', () => {
  assert.deepEqual(factRows(undefined), [])
  assert.deepEqual(factRows({}), [])
})

test('a short quote is shown whole, with whitespace collapsed', () => {
  assert.equal(quoteSnippet('Plans start at\n  $29 per user per month.', '$29'), 'Plans start at $29 per user per month.')
})

test('a long quote is cut to a window around the value, with … at each cut end', () => {
  const before = 'Our plans are designed for teams of every size and every budget, from startups to enterprises. '
  const after = ' Every plan includes unlimited projects, priority support and a dedicated customer success manager for you.'
  const snippet = quoteSnippet(`${before}Solo starts at $19 per user / month${after}`, '$19 per user / month')
  assert.match(snippet, /\$19 per user \/ month/)
  assert.ok(snippet.startsWith('…') && snippet.endsWith('…'), snippet)
  assert.ok(snippet.length <= SNIPPET_LENGTH + 2, snippet.length)
})

test('only the end that was cut gets a …', () => {
  const head = `Solo is $19 per user / month. ${'More detail about every plan we sell and what it includes. '.repeat(5)}`
  const atStart = quoteSnippet(head, '$19 per user / month')
  assert.ok(!atStart.startsWith('…') && atStart.endsWith('…'), atStart)
  const tail = `${'More detail about every plan we sell and what it includes. '.repeat(5)}Solo is $19 per user / month.`
  const atEnd = quoteSnippet(tail, '$19 per user / month')
  assert.ok(atEnd.startsWith('…') && !atEnd.endsWith('…'), atEnd)
})

test('the cut never lands inside a word or inside the value', () => {
  const quote = `${'abcdefghij '.repeat(20)}$19 per user / month ${'klmnopqrst '.repeat(20)}`
  const snippet = quoteSnippet(quote, '$19 per user / month')
  const words = snippet.replace(/…/g, '').trim().split(' ')
  for (const word of words) assert.ok(['abcdefghij', 'klmnopqrst', '$19', 'per', 'user', '/', 'month'].includes(word), word)
  assert.match(snippet, /\$19 per user \/ month/)
})

test('a value that is not in the quote shows the start of the quote', () => {
  const snippet = quoteSnippet('word '.repeat(60), 'nonexistent')
  assert.ok(!snippet.startsWith('…') && snippet.endsWith('…'))
})

test('rows show the snippet, not the whole quote', () => {
  const long = `${'Intro words here. '.repeat(15)}Plans start at $29 per user per month. ${'Closing words here. '.repeat(15)}`
  const [row] = factRows({ fields: [{ field: 'starting_price', verdict: 'match', official_value: '$29 per user per month', official_quote: long }] })
  assert.match(row.officialQuote, /\$29 per user per month/)
  assert.ok(row.officialQuote.length <= SNIPPET_LENGTH + 2)
})
