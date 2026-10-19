// Keeps the result page readable for people who are not technical. It renders the real page
// from three saved real results, reads every piece of text a visitor can see, and fails when
// that text breaks the Customer-facing UI rules in CLAUDE.md:
//   - no ( ) [ ] { }, no dashes, no vertical bars, no slash used to separate things
//   - none of our internal words
// It also reads the two new blocks of the page, the bars of where AI looked and the website readiness.
// Words that come from other websites (page titles, quotes, addresses) are marked
// data-external in the page. They are checked for the characters, and left out of the word check.
//
// The three results:
//   result_feroot.json                     a company AI never mentions
//   result_close_with_competitors.json     a company that is mentioned, with three competitors entered
//   result_clio_unreadable.json            a website that blocked our visit, so nothing could be read
import { after, before, describe, test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { startRenderer, visibleText } from './testSupport/pageText.js'

const FORBIDDEN_CHARACTERS = /[()[\]{}|—–−]/
const FORBIDDEN_SEPARATORS = /\s-\s|--|\s\/\s/
const INTERNAL_WORDS =
  /\b(reliance|presence|gap|coverage|citations?|unknown|layers?|method|versions?|cache|cached|engine|judg(?:e)?ments?|beta|confidence interval|wilson)\b/i

const fixture = (name) => JSON.parse(readFileSync(new URL(`./fixtures/${name}`, import.meta.url), 'utf8'))
const RESULTS = {
  'Feroot, never mentioned': fixture('result_feroot.json'),
  'Close, with competitors': fixture('result_close_with_competitors.json'),
  'Clio, website could not be read': fixture('result_clio_unreadable.json'),
}

let renderer
let ResultPage
let Hero
let SearchForm
let EmailGate

before(async () => {
  renderer = await startRenderer()
  ;[ResultPage, Hero, SearchForm, EmailGate] = (
    await Promise.all(
      ['ResultPage', 'Hero', 'SearchForm', 'EmailGate'].map((name) => renderer.load(`/src/components/${name}.jsx`)),
    )
  ).map((module) => module.default)
})

after(async () => {
  await renderer.close()
})

function page(result, props = {}) {
  return renderToStaticMarkup(createElement(ResultPage, { result, unlocked: true, onUnlock: () => {}, ...props }))
}

function assertClean(text, label) {
  const bad = text.match(FORBIDDEN_CHARACTERS)
  assert.equal(bad, null, `${label}: forbidden character ${bad && JSON.stringify(bad[0])} near "${bad && text.slice(Math.max(0, bad.index - 40), bad.index + 40)}"`)
  const separator = text.match(FORBIDDEN_SEPARATORS)
  assert.equal(separator, null, `${label}: separator ${separator && JSON.stringify(separator[0])} near "${separator && text.slice(Math.max(0, separator.index - 40), separator.index + 40)}"`)
}

function assertNoInternalWords(text, label) {
  const word = text.match(INTERNAL_WORDS)
  assert.equal(word, null, `${label}: internal word "${word && word[0]}" near "${word && text.slice(Math.max(0, word.index - 50), word.index + 50)}"`)
}

describe('every piece of text on the result page follows the rules', () => {
  for (const [name, result] of Object.entries(RESULTS)) {
    // closed: what a visitor sees first. open and unlocked: everything behind "Show details", every row expanded.
    // open and locked: the same page behind the email box.
    const states = {
      'details closed': {},
      'details open and every row expanded': { detailsOpen: true, startOpen: true },
      'details open, locked, rows expanded': { detailsOpen: true, startOpen: true, unlocked: false },
    }
    for (const [state, props] of Object.entries(states)) {
      test(`${name}, ${state}`, () => {
        const { all, ours } = visibleText(page(result, props))
        assert.ok(all.length > 100, 'the page has text')
        assertClean(all, `${name}, ${state}`)
        assertNoInternalWords(ours, `${name}, ${state}`)
      })
    }
  }

  test('the banner, the form and the email box', () => {
    for (const element of [createElement(Hero), createElement(SearchForm, { onSubmit: () => {}, loading: false }), createElement(EmailGate, { onUnlock: () => {} })]) {
      const { all, ours } = visibleText(renderToStaticMarkup(element))
      assertClean(all, 'form')
      assertNoInternalWords(ours, 'form')
    }
  })

  test('the competitor box asks for names only', () => {
    const { all } = visibleText(renderToStaticMarkup(createElement(SearchForm, { onSubmit: () => {}, loading: false })))
    assert.ok(all.includes('Competitors, optional'))
    assert.ok(all.includes('e.g. HubSpot, Pipedrive'))
    assert.ok(all.includes('Up to 5 names, separated by commas.'))
    assert.ok(!all.includes('domain.com'))
  })
})

describe('error and empty messages follow the rules too', () => {
  test('messages from the diagnose request', async () => {
    const { runDiagnosis } = await renderer.load('/src/api.js')
    const real = globalThis.fetch
    const messages = []
    try {
      globalThis.fetch = async () => {
        throw new Error('offline')
      }
      messages.push((await runDiagnosis('A', 'a.com').catch((error) => error)).message)
      globalThis.fetch = async () => ({ ok: false, status: 500, text: async () => 'server error' })
      messages.push((await runDiagnosis('A', 'a.com').catch((error) => error)).message)
      globalThis.fetch = async () => ({ ok: false, status: 422, text: async () => JSON.stringify({ detail: 'Add at most 5 competitors.' }) })
      messages.push((await runDiagnosis('A', 'a.com', 'a, b, c, d, e, f').catch((error) => error)).message)
    } finally {
      globalThis.fetch = real
    }
    const originalError = console.error
    for (const message of messages) {
      assert.ok(message && message.length > 10, 'a message was produced')
      assertClean(message, 'api message')
      assertNoInternalWords(message, 'api message')
    }
    console.error = originalError
  })

  test('messages written in the form and the email box', async () => {
    const sources = ['components/SearchForm.jsx', 'components/EmailGate.jsx']
    for (const file of sources) {
      const code = readFileSync(new URL(`./${file}`, import.meta.url), 'utf8')
      for (const match of code.matchAll(/set(?:Error)\(\s*[`'"]([^`'"]+)[`'"]/g)) {
        const message = match[1].replace(/\$\{[^}]*\}/g, '5')
        assertClean(message, `${file} message`)
        assertNoInternalWords(message, `${file} message`)
      }
    }
  })
})

describe('what the page says', () => {
  test('Feroot: the headline, the range, the chart and the conclusion come first and need no email', () => {
    const { all } = visibleText(page(RESULTS['Feroot, never mentioned'], { unlocked: false }))
    assert.ok(all.includes('This result will stay the same for the next 24 hours.'))
    assert.ok(all.includes('AI mentioned you in 0 of 15 answers.'))
    assert.ok(all.includes('The true rate is likely between 0% and 20%.'))
    assert.ok(all.includes('Where AI looked for answers'))
    assert.ok(all.includes('We asked AI 15 questions your customers ask. To answer them, AI read these kinds of pages.'))
    assert.ok(all.includes('Is your website ready for AI?'))
    assert.ok(all.includes('You are missing most from'))
    assert.ok(all.includes('Show details'))
    assert.ok(!all.includes('Enter your email'), 'the email box only appears behind Show details')
  })

  test('Close: the competitors are listed, most mentions first, with your own line bold', () => {
    const result = RESULTS['Close, with competitors']
    const markup = page(result)
    const { all } = visibleText(markup)
    const lines = result.competitor_comparison.entries.map((entry) => `${entry.name} ${entry.mentions} of ${entry.total} answers`)
    assert.equal(lines.length, 4, 'you and three competitors')
    let last = -1
    for (const line of lines) {
      const at = all.indexOf(line)
      assert.ok(at > last, `${line} is listed, after the ones with more mentions`)
      last = at
    }
    const counts = result.competitor_comparison.entries.map((entry) => entry.mentions)
    assert.deepEqual(counts, [...counts].sort((a, b) => b - a), 'sent most mentions first')
    assert.match(markup, /class="competitor-you"><span class="competitor-name"[^>]*>Close</)
  })

  test('Clio: a website that could not be read says so in plain words, with no chart', () => {
    const { all } = visibleText(page(RESULTS['Clio, website could not be read']))
    assert.ok(all.includes('Not enough data'))
    assert.ok(all.includes('We could not open your homepage at clio.com'))
    assert.ok(!all.includes('Where AI looked for answers'))
    assert.ok(all.includes('We could not read your website.'))
    assert.ok(!all.includes('out of 100'), 'no score when nothing could be read')
    assert.ok(all.includes('AI did not use any websites as sources for these questions'))
  })

  test('the details stay behind the button until it is pressed, and behind the email box until it is entered', () => {
    const closed = page(RESULTS['Feroot, never mentioned'])
    assert.ok(!closed.includes('layer-table'))
    const locked = page(RESULTS['Feroot, never mentioned'], { detailsOpen: true, unlocked: false })
    assert.ok(locked.includes('email-gate'))
    const open = page(RESULTS['Feroot, never mentioned'], { detailsOpen: true, unlocked: true })
    assert.ok(open.includes('layer-table'))
  })

  test('the same result renders to the same page every time', () => {
    for (const result of Object.values(RESULTS)) {
      assert.equal(page(result, { detailsOpen: true, startOpen: true }), page(structuredClone(result), { detailsOpen: true, startOpen: true }))
    }
  })
})
