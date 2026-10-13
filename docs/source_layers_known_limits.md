# Source layers: known limits (list v2, page type rules v1, method v2)

The layered diagnosis (`backend/diagnose_service/source_layers.py`, with the
domain lists and page type keywords in `engines/_source_layers.py`) groups
the sources AI cited into reference sites, review sites and media, community
sites, the company's own site, and other companies' pages (split into best-of
lists, guides, and product pages), and works out how much AI relies on each
and how often the company shows up there. Everything is rules, no LLM. The
lists and keywords are our judgment and are versioned.

This file records what is known to be wrong or weak, each with a real example
from live runs in October 2026 on feroot.com, close.com, linear.app,
freshbooks.com and vidyard.com.

## The sample is smaller than it looks

- **The 3 repeats of a question usually cite the same sources.** Each of the
  5 buyer questions is asked 3 times, but in the five test runs the 3 repeats
  of a question cited identical source lists for 4 of 5 questions on
  feroot.com, 3 of 5 on close.com, 5 of 5 on linear.app, 5 of 5 on
  freshbooks.com, and 4 of 5 on vidyard.com. Citation counts are therefore
  mostly multiples of 3, and **the 15 answers are closer to 5 independent
  samples**. Reliance percentages are less precise than their decimals
  suggest, and one question's sources can move a layer by 20 points.
- Perplexity returns about 10 distinct sources per answer, so every company
  has about 150 citations; this is a property of the data source, not of the
  company.

## Does a page mention the company? (presence)

- **The "capitalized name opens a heading or list item" rule is the weak
  point for names that are ordinary words.** On close.com, 10 of the 12 pages
  judged to mention the company were judged so by this rule, and all 10 were
  wrong: the heading or list item was the verb "Close" ("Close deals faster",
  "Close this out", "Close Search" on hubspot.com product pages, "Close more
  deals, faster with the right CRM" on monday.com). Only the 2 ringy.com pages
  (a real link to close.com) were correct. This inflates presence for
  close.com in every group. On linear.app the same rule found 2 real
  mentions ("15. Linear" on monday.com; "Linear streamlines issue management"
  on notion.com), and "Linear regression" or "Linear algebra" headings would
  be false positives in the same way.
- **Real mentions in running text are missed for names that are ordinary
  words.** linear.app: contentsquare.com ("push it straight to Jira, Asana, or
  Linear") and uservoice.com ("Aha!, Productboard, and Linear all have
  feedback intake capabilities") both mention the company but only in a
  sentence, so they count as "doesn't mention you".
- **Some fetched pages are not the real page.** support.mozilla.org returned
  a bot challenge titled "Client Challenge" for three feroot.com pages (9
  citations). They count as checked, and as not mentioning the company.
- **A page that can't be fetched is "unknown", and a group made only of
  unknown pages has no gap.** freshbooks.com: britannica.com answered with an
  HTTP error, so the reference group shows "Couldn't check". Sites that block
  automated visits (common for community sites such as LinkedIn and Reddit,
  and for large publishers) are affected the same way.
- Only the 60 most-cited pages per company are fetched. In the five test runs
  that covered 92% to 100% of the citations outside the company's own site
  (`coverage` in the data), but a company with a longer tail would be
  checked less completely.
- The text is read from the raw HTML (`method="full"`), so a mention that is
  only added by JavaScript is not seen.

## Page types of other companies' pages

The page type comes from keywords in the URL (and a subdomain label such as
`support.` or `help.`) and in the page title. Whole-word matching, no LLM.

- **"best", "top", "review" in a title make a guide a "list".**
  mixmax.com "The Best Way to Track Sales Follow-Ups (A Guide)" and
  salesforce.com "What's the Best Way To Follow Up With a Sales Lead?" are
  guides but count as lists; learn.microsoft.com "Best practices for Agile
  product management" and shopify.dev ".../best-practices/..." are
  documentation but count as lists.
- **Guides without a keyword are product pages.** web.dev "Load Third-Party
  JavaScript | Articles" (path `/articles/`), chilipiper.com
  `/article/speed-to-lead-statistics`, revenue.io `/inside-sales-glossary/...`.
  Keywords match whole words, so `knowledgebase.constantcontact.com` does not
  match "knowledge" and is a product page.
- **Vendor documentation sites are guides only when the host or path has a
  keyword.** support.microsoft.com, learn.microsoft.com, docs.stripe.com and
  help.* sites are guides; web.dev is not (see above).
- A review or comparison page that sits on the company's own competitor's
  site is a "list" (correct), but a competitor's product page that happens to
  say "Top features" in its title is also a "list".

## Domain lists (v2)

- Anything not on a list is "other companies", which then goes by page type.
  Government and university sites are matched by `.gov` / `.edu` (and country
  forms such as `.gov.uk`, `.edu.au`) only; other countries' equivalents are
  not recognised.
- News sites are an incomplete list of well-known outlets plus the ones seen
  in the test runs (for example sanluisobispo.com). Other local and trade
  newspapers fall into "other companies" until added.
- Any `community.` subdomain is treated as a community site, including a
  vendor's own support forum.
- Vendor documentation (support.microsoft.com, learn.microsoft.com,
  docs.stripe.com, web.dev) stays in "other companies" on purpose; by page
  type it lands in guides, or in product pages when it has no keyword.
- Review sites that publish both reviews and the vendor's own marketing (for
  example a vendor listing page on G2) are all "review sites and media".

## What the numbers mean

- **Biggest gap** is the group with the largest reliance x (1 - presence). It
  says where AI reads a lot and the company is rarely there; it does not say
  the page would change an AI answer if the company were added.
- On all five test companies the biggest gap was "guides". Guides are the
  largest group because the keyword list is broad (blog, docs, help, support,
  learn and so on); the share of lists, guides and product pages depends on
  those keywords as much as on what AI reads.
- Reliance and presence are not independent samples: pages cited 3 times
  count 3 times (see the first section).
