import { gapConclusion } from '../utils/resultPage.js'

// One sentence that says which kind of page AI read is missing you the most, and under it the
// pages there that do not mention you, each a link. Props: sourceBars and sourceLayers (result).
export default function GapConclusion({ sourceBars, sourceLayers }) {
  const { sentence, pages } = gapConclusion(sourceBars, sourceLayers)

  return (
    <div className="gap-conclusion">
      <p>{sentence}</p>
      {pages.length > 0 && (
        <ul>
          {pages.map((page) => (
            <li key={page.key}>
              <a href={page.href} target="_blank" rel="noreferrer">
                <span data-external="true">{page.title}</span>
              </a>{' '}
              <span className="gap-conclusion-site">on <span data-external="true">{page.site}</span></span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
