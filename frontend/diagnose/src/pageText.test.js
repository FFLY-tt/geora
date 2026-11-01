// Keeps the result page readable for people who are not technical. It renders the real page
// from three saved real results, reads every piece of text a visitor can see, and fails when
// that text breaks the Customer-facing UI rules in CLAUDE.md:
//   - no ( ) [ ] { }, no dashes, no vertical bars, no slash used to separate things
//   - none of our internal words
// It also checks that the text above the Show details button is exactly the allowed text and no more.
// Words that come from other websites (page titles, quotes, addresses) are marked
// data-external in the page. They are checked for the characters, and left out of the word check.
//
// The three results:
//   result_feroot.json                     a company AI never mentions
//   result_close_with_competitors.json     a company that is mentioned, with three competitors entered
//   result_clio_unreadable.json            a website that blocked our visit, so nothing could be read
//   result_freshbooks.json                 a company that is mentioned a lot
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
  'FreshBooks, mostly mentioned': fixture('result_freshbooks.json'),
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

// Everything above the "Show details" button, written out from the saved numbers and nothing else.
// The page may show exactly this text and no more.
function expectedAboveTheButton(result) {
  const parts = ['This result will stay the same for the next 24 hours.']
  const citation = result.dimensions.find((dim) => dim.dimension === 'citation_visibility')
  if (citation.score === -1) {
    parts.push('Not enough data', citation.details.plain_finding.what_we_found)
  } else {
    const details = citation.details
    parts.push(`AI mentioned you in ${details.mentions} of ${details.total} answers.`)
    parts.push(`The true rate is likely between ${Math.round(details.ci_low * 100)}% and ${Math.round(details.ci_high * 100)}%.`)
  }
  for (const entry of result.competitor_comparison?.entries ?? []) parts.push(entry.name, `${entry.mentions} of ${entry.total}`)

  // Other kinds of page need an opened page. Your own website always has its row, last: "N of N", or just "0".
  if (result.source_bars) {
    const rows = result.source_bars.rows.filter((row) => row.key !== 'own_site' && row.pages > 0).sort((a, b) => b.pages - a.pages)
    parts.push('Where AI looked for answers')
    for (const row of rows) parts.push(row.name, `${row.mention_pages} of ${row.pages}`)
    const own = result.source_bars.rows.find((row) => row.key === 'own_site' && row.pages > 0)
    parts.push('Your own website', own ? `${own.pages} of ${own.pages}` : '0')
  }

  const items = (result.site_readiness?.items ?? []).filter((item) => item.score !== null)
  if (items.length > 0) {
    parts.push('Is your website ready for AI?', `${result.site_readiness.total} out of 100`)
    for (const item of items) parts.push(item.name, String(item.score), item.level)
  }
  parts.push('Show details')
  return parts.join(' ')
}

describe('what the page says above the Show details button', () => {
  for (const [name, result] of Object.entries(RESULTS)) {
    test(`${name}: only the allowed text`, () => {
      const { all } = visibleText(page(result, { unlocked: false }))
      assert.equal(all, expectedAboveTheButton(result))
    })
  }

  test('no explanation, no legend, no conclusion box, no note about pages we could not open', () => {
    for (const name of ['Feroot, never mentioned', 'Close, with competitors', 'FreshBooks, mostly mentioned']) {
      const { all } = visibleText(page(RESULTS[name]))
      for (const unwanted of [/could not/i, /did not check/i, /We asked AI/, /pages? mentions? you/, /missing most/i, /Start by fixing/, /in good shape/, /Keep it that way/]) {
        assert.doesNotMatch(all, unwanted, `${name}: ${unwanted}`)
      }
    }
  })

  test('Feroot: a kind with no opened page has no row, and so does a check with no result', () => {
    const feroot = visibleText(page(RESULTS['Feroot, never mentioned'])).all
    assert.ok(feroot.includes('Best tools lists 1 of 27'))
    assert.ok(feroot.includes('Guides and articles 0 of 14'))
    assert.ok(!feroot.includes('Review sites and news'), 'all its pages could not be opened')
    const freshbooks = visibleText(page(RESULTS['FreshBooks, mostly mentioned'])).all
    assert.ok(!freshbooks.includes('Guides and articles'), 'none of its pages were checked')
    assert.ok(!freshbooks.includes('Up to date'), 'that check has no result')
    assert.ok(freshbooks.includes('56 out of 100'), 'the total uses the checks that have a result')
  })

  test('your own website is all blue and reads as all of its pages when AI used it', () => {
    const markup = page(RESULTS['Close, with competitors'])
    assert.ok(visibleText(markup).all.includes('Your own website 2 of 2'))
    assert.ok(visibleText(page(RESULTS['FreshBooks, mostly mentioned'])).all.includes('Your own website 3 of 3'))
  })

  test('your own website still has its row, with an empty bar and just 0, when AI never used it', () => {
    const markup = page(RESULTS['Feroot, never mentioned'])
    assert.ok(visibleText(markup).all.includes('Where AI looked for answers Best tools lists 1 of 27 Guides and articles 0 of 14 Your own website 0 Is your website'))
    assert.match(markup, /Your own website<\/span><span class="bar-track bar-track-full"><\/span><span class="bar-number">0</)
  })

  test('your own website is always the last row of the bars', () => {
    for (const name of ['Feroot, never mentioned', 'Close, with competitors', 'FreshBooks, mostly mentioned']) {
      const markup = page(RESULTS[name])
      const names = [...markup.matchAll(/<span class="bar-name">([^<]*)<\/span>/g)].map((m) => m[1])
      const own = names.indexOf('Your own website')
      assert.ok(own >= 0)
      assert.ok(own < names.indexOf('Answers customer questions'))
      // the last name of the first group is your own website: the next name belongs to the readiness bars
      assert.equal(names[own + 1], 'Answers customer questions', name)
    }
  })

  test('with no opened page in any other kind the block has only your own website', () => {
    const result = structuredClone(RESULTS['Feroot, never mentioned'])
    for (const row of result.source_bars.rows) row.pages = 0
    const { all } = visibleText(page(result))
    assert.ok(all.includes('Where AI looked for answers Your own website 0 Is your website'))
  })

  test('with no source bars at all (AI used no sources) there is no block', () => {
    const result = structuredClone(RESULTS['Feroot, never mentioned'])
    result.source_bars = null
    assert.ok(!visibleText(page(result)).all.includes('Where AI looked for answers'))
  })

  test('no score for any check means no "Is your website ready for AI?" block', () => {
    const result = structuredClone(RESULTS['Feroot, never mentioned'])
    for (const item of result.site_readiness.items) item.score = null
    result.site_readiness.total = null
    assert.ok(!visibleText(page(result)).all.includes('Is your website ready for AI?'))
  })

  test('the competitor list is most mentions first, with your own line bold', () => {
    const result = RESULTS['Close, with competitors']
    const markup = page(result)
    const counts = result.competitor_comparison.entries.map((entry) => entry.mentions)
    assert.deepEqual(counts, [...counts].sort((a, b) => b - a), 'sent most mentions first')
    assert.match(markup, /class="competitor-you"><span class="competitor-name"[^>]*>Close</)
    assert.ok(!visibleText(page(RESULTS['Feroot, never mentioned'])).all.includes('HubSpot'), 'no competitors entered, no list')
  })

  test('the bars are plain HTML, not a chart', () => {
    const markup = page(RESULTS['Close, with competitors'])
    assert.ok(!markup.includes('<canvas'))
    assert.ok(markup.includes('class="bar-row"'))
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
