// Helpers for the page text test: render a real component to HTML with the real built-in
// bundler transform, and read back the text a visitor can see.
import { createServer } from 'vite'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')

export async function startRenderer() {
  const server = await createServer({
    root: ROOT,
    configFile: path.join(ROOT, 'vite.config.js'),
    server: { middlewareMode: true },
    appType: 'custom',
    logLevel: 'error',
    optimizeDeps: { noDiscovery: true },
  })
  return {
    load: (file) => server.ssrLoadModule(file),
    close: () => server.close(),
  }
}

const ENTITIES = { '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&#x27;': "'", '&#39;': "'", '&nbsp;': ' ' }

function decode(text) {
  return text.replace(/&(?:amp|lt|gt|quot|nbsp|#x27|#39);/g, (entity) => ENTITIES[entity])
}

/**
 * The text of rendered markup: { all, ours }. `all` is everything a visitor can read, including
 * placeholders and labels. `ours` leaves out the text marked data-external, which is words from
 * other websites (page titles, quotes, addresses) that we did not write.
 */
export function visibleText(markup) {
  const attributes = [...markup.matchAll(/\s(?:placeholder|aria-label|title|alt)="([^"]*)"/g)].map((match) => decode(match[1]))
  const clean = (html) => decode(html.replace(/<(script|style)[\s\S]*?<\/\1>/g, ' ').replace(/<[^>]+>/g, ' '))
  const squeeze = (text) => text.replace(/\s+/g, ' ').trim()
  const withoutExternal = markup.replace(/<span[^>]*data-external="true"[^>]*>[\s\S]*?<\/span>/g, ' ')
  return {
    all: squeeze([clean(markup), ...attributes].join(' ')),
    ours: squeeze([clean(withoutExternal), ...attributes].join(' ')),
  }
}
