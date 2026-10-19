import { gapConclusion } from '../utils/resultPage.js'

// One sentence that points at the corner of the chart where the company is missing most,
// and under it the pages AI reads there that do not mention the company, each a link.
export default function GapConclusion({ radar, sourceLayers }) {
  const { sentence, pages } = gapConclusion(radar, sourceLayers)

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
