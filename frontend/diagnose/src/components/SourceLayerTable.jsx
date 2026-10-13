import { useState } from 'react'
import AccordionItem from './AccordionItem'
import { citedText, dimensionsForLayer, layerRows, pageHref, pageStatusText, pageTitle, shortUrl, unplacedDimensions } from '../utils/sourceLayers.js'

// One row per kind of website AI read: how much AI leaned on it, how much
// of it mentions the company, and the status. Opening a row lists the
// pages AI cited (with the passage that mentions the company), and for
// the company's own site and the review sites it also shows the older
// diagnostic details that belong to that layer.
//
// Props: sourceLayers (result.source_layers), dimensions (result.dimensions).
// The most-cited pages are shown first; the rest are one click away.
const PAGES_SHOWN = 8

function PageList({ layer, pages }) {
  const [showAll, setShowAll] = useState(false)
  if (pages.length === 0) {
    return <p className="layer-empty">AI didn't cite any page from this group.</p>
  }
  const visible = showAll ? pages : pages.slice(0, PAGES_SHOWN)
  return (
    <>
      <ul className="layer-pages">
        {visible.map((page) => (
          <li key={page.url}>
            <a className="layer-page-url" href={pageHref(page.url)} target="_blank" rel="noreferrer">
              {pageTitle(page)}
            </a>
            <span className="layer-page-meta">{shortUrl(page.url)}</span>
            <span className="layer-page-meta">
              {citedText(page.citations)} · {pageStatusText(layer, page)}
            </span>
            {page.snippet && <span className="layer-page-snippet">“…{page.snippet}…”</span>}
          </li>
        ))}
      </ul>
      {pages.length > PAGES_SHOWN && (
        <button type="button" className="layer-show-all" onClick={() => setShowAll((prev) => !prev)}>
          {showAll ? 'Show fewer pages' : `Show all ${pages.length} pages`}
        </button>
      )}
    </>
  )
}

function LayerRow({ row, dimensions }) {
  const [open, setOpen] = useState(false)
  const details = dimensionsForLayer(row.layer, dimensions)

  return (
    <div className={`layer-row layer-status-${row.status}`}>
      <button type="button" className="layer-row-header" onClick={() => setOpen((prev) => !prev)} aria-expanded={open}>
        <span className="layer-cell layer-cell-name">{row.label}</span>
        <span className="layer-cell layer-cell-number">{row.relianceText}</span>
        <span className="layer-cell layer-cell-number">{row.presenceText}</span>
        <span className="layer-cell layer-cell-status">{row.statusText}</span>
        <span className="layer-cell layer-cell-toggle">{open ? '−' : '+'}</span>
      </button>

      {open && (
        <div className="layer-body">
          <p className="layer-hint">{row.hint}</p>
          <PageList layer={row.layer} pages={row.pages} />
          {details.length > 0 && (
            <div className="layer-details">
              {row.layer === 'own_site' && <h4 className="layer-details-title">Your website details</h4>}
              {details.map((dim) => (
                <AccordionItem key={dim.dimension} dimension={dim} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default function SourceLayerTable({ sourceLayers, dimensions }) {
  const rows = layerRows(sourceLayers)
  const leftovers = unplacedDimensions(dimensions)

  return (
    <div className="layer-table">
      <div className="layer-table-head">
        <span className="layer-cell layer-cell-name">Where AI reads</span>
        <span className="layer-cell layer-cell-number">How much AI relies on it</span>
        <span className="layer-cell layer-cell-number">How often you appear</span>
        <span className="layer-cell layer-cell-status">Status</span>
        <span className="layer-cell layer-cell-toggle" />
      </div>
      {rows.map((row) => (
        <LayerRow key={row.layer} row={row} dimensions={dimensions} />
      ))}
      {leftovers.length > 0 && (
        <div className="layer-details">
          {leftovers.map((dim) => (
            <AccordionItem key={dim.dimension} dimension={dim} />
          ))}
        </div>
      )}
    </div>
  )
}
