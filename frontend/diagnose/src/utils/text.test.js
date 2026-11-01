import { test } from 'node:test'
import assert from 'node:assert/strict'
import { cleanPageTitle, plainText, siteName } from './text.js'

test('the site name at the end of a title is removed', () => {
  assert.equal(cleanPageTitle('Top 8 Cookie Scanner Tools 2026 | Trackingplan'), 'Top 8 Cookie Scanner Tools 2026')
  assert.equal(cleanPageTitle('Best CRM Tools for Sales Teams – Sybill'), 'Best CRM Tools for Sales Teams')
  assert.equal(cleanPageTitle('Best CRM Tools for Sales Teams — Sybill'), 'Best CRM Tools for Sales Teams')
  assert.equal(cleanPageTitle('Best CRM Tools for Sales Teams - Sybill'), 'Best CRM Tools for Sales Teams')
  assert.equal(cleanPageTitle('Best CRM Tools for Sales Teams—Sybill'), 'Best CRM Tools for Sales Teams')
})

test('brackets and what is inside them are removed', () => {
  assert.equal(cleanPageTitle('9 Tools Evaluated (2026)'), '9 Tools Evaluated')
  assert.equal(cleanPageTitle('10 Best Billing Software [2026] | Keka HR'), '10 Best Billing Software')
  assert.equal(cleanPageTitle('Cookie Tools {updated} for Stores (and more)'), 'Cookie Tools for Stores')
})

test('a long part after a separator is kept, with a comma in place of the separator', () => {
  assert.equal(
    cleanPageTitle('Vimeo vs Wistia – Which Is Better for Business Video Hosting and Marketing'),
    'Vimeo vs Wistia, Which Is Better for Business Video Hosting and Marketing',
  )
})

test('two site names at the end are both removed', () => {
  assert.equal(cleanPageTitle('Best Tools | Blog | Acme'), 'Best Tools')
})

test('a title that is only a site name or only brackets leaves nothing', () => {
  assert.equal(cleanPageTitle('(2026)'), '')
  assert.equal(cleanPageTitle(''), '')
  assert.equal(cleanPageTitle(null), '')
})

test('a title never keeps the characters the page rules forbid', () => {
  for (const title of ['A | B | C', 'A – B – C long words that stay here as a part', 'A (b) [c] {d} — e', 'Dev / Ops tools for teams | Site']) {
    assert.doesNotMatch(cleanPageTitle(title), /[()[\]{}|–—]/, title)
  }
})

test('site name shows the host without www', () => {
  assert.equal(siteName('https://www.trackingplan.com/blog/cookie-scanners'), 'trackingplan.com')
  assert.equal(siteName('sybill.ai/blogs/best-crm-tools'), 'sybill.ai')
  assert.equal(siteName('http://news.example.org?x=1'), 'news.example.org')
})

test('text from a website keeps its words but loses brackets, dashes and bars', () => {
  assert.equal(plainText('Plans start at $29 (billed yearly) — cancel anytime'), 'Plans start at $29 billed yearly, cancel anytime')
  assert.equal(plainText('Free plan | Pro plan'), 'Free plan, Pro plan')
  assert.equal(plainText('Solo – $19 per user'), 'Solo, $19 per user')
  assert.equal(plainText('Built for sales-led teams'), 'Built for sales-led teams')
  assert.equal(plainText('Cloud / on premise'), 'Cloud, on premise')
})
