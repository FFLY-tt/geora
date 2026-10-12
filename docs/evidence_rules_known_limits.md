# evidence_density rules: known limits (v3.2-rules)

`backend/diagnose_service/engines/evidence_engine.py` scores evidence with
deterministic code rules. The rules favour precision over recall: a wrong
item inflates the score and misleads the user, a missed item only makes the
score conservative. This file lists what the current version is known to
miss or get wrong, each with a real example seen on a live site in October
2026.

Sites used while writing the rules (development set): close.com, feroot.com,
linear.app, freshbooks.com, clio.com, vidyard.com.
Holdout sets, only used to check the rules, never to change them afterwards:
v3.1: jobber.com, hootsuite.com, klue.com, unbounce.com, waveapps.com, tophat.com.
v3.2: ada.cx, d2l.com, axonify.com, geotab.com, partnerstack.com, nulogy.com.

## Design change in v3.2: customer stories instead of customer logos

Up to v3.1 the third category was "named customers", found mostly from logo
image alt text. On the v3.1 holdout set only about 60% of those items were
real customers. Alt text cannot tell a customer logo from an avatar, an
illustration, an icon, or a review site badge, and more rules cannot fix
that. More importantly, an AI assistant reading a website cannot see logo
images at all, so a logo is not evidence an AI can quote.

The category is now **customer stories**: distinct pages linked one level
below the customers page (`/customers/acme` under `/customers`). A story page
is text an AI can read, cite, and link to. This is our judgment, written into
the code (`CUSTOMER_STORIES_REASON`) and into every result's details. Logos
are still listed in `details.customer_logos_reference`, never scored.

The logo problems found in v3.1 no longer affect the score. They are kept
here because the reference list still shows them:

| v3.1 # | Problem in the logo reference list | Real example |
|---|---|---|
| W1 | Image alt texts inside a section that mentions "customers": avatars, illustrations, feature icons, UI screenshots. | jobber.com: "Stacy Flanagan avatar", "A man holding a cell phone"; close.com: "Slack message from a Close customer"; waveapps.com: "Invoicing", "Wallet"; freshbooks.com: "Chevron Up"; hootsuite.com: "rocketship"; unbounce.com: "Ecommerce stores" |
| W2 | A review site badge in a logo strip. | hootsuite.com homepage: "G2" |
| M3 | Customer lists introduced by wording other than "trusted by" / "customers include". | feroot.com: "Security teams at Instacart, Reddit, and Gusto use Feroot" |
| M4 | An entry holding several names is dropped, never split. | vidyard.com trust page: "trusted by Google Slack Zoom Gong Microsoft Salesloft Theta Lake" |
| M5 | Logos with no alt text, or in a strip with no customer / trusted signal. | klue.com: no logo found on the homepage or /customers |

## Fixed in v3.2

| v3.1 # | What was wrong | Fix |
|---|---|---|
| W3 (part) | Funding, grants, founding year, years of experience counted as numbers (klue.com "Over $81M Raised", jobber.com "Our $150,000 grant program", "$250K Available in 2026"). | Sentences with grant, funding, raised, Series A to F, valuation, founded, years of experience, available in are excluded. |
| W6 (part) | A standard inside an FAQ question (unbounce.com "Is Unbounce GDPR compliant?"). | Third-party names inside a question are not counted. |
| W7 | "0%" placeholders from JavaScript counters (tophat.com "0% say Top Hat is easy to use"). | A value of 0 is never counted. |
| W8 | One person counted twice when the title was written differently (close.com "Jem Millett, Director of Admissions @ ..." and "Jem Millett Senior Director of Admissions, ..."). | A name stops at the first title word (Senior, Former, Sr., role words), so one person is one item. |

## Misses (evidence on the page that is not counted)

| # | What is missed | Real example |
|---|---|---|
| M1 | Reviews attributed only to a job title, with no person or company name. | feroot.com homepage: "Chief Security Officer Verified G2 review 5 / 5" |
| M2 | Attributions without the comma or "@" / "at" the rules expect, and without an HTML caption. | klue.com homepage: "Jordan Glenn Sr. Business Process Analyst Case Study 72% Seller Adoption" |
| M6 | Key pages the homepage does not link to with a recognisable path. | freshbooks.com: no customers / case studies page found from the homepage links, so customer stories = 0. |
| M7 | Any site that refuses our request. The score is -1 with the reason, not a guess. | clio.com and ada.cx: "blocked: HTTP 403" |
| M8 | Numbers whose value is filled in by JavaScript after the page loads; only the raw HTML is read. | tophat.com: the real value behind "0% say Top Hat is easy to use" is never seen. |
| M9 | Customer stories that are not exactly one level below the selected customers page. | axonify.com: /customers was read but 0 stories counted; geotab.com: /success-stories was read but 0 stories counted. The story links point elsewhere (not checked one by one). |
| M10 | A long run of text without sentence punctuation that ends in "?" is treated as one question, so every standard in it is dropped. | trust.vidyard.com: a 1,970-character run starting "Compliance and Attestations GDPR Microsoft SSPA SOC 2 Type 2 ..." and ending "Need to Report an Issue with a Subprocessor?" lost SOC 2 and GDPR. |

## Wrong finds (counted, but not real evidence)

| # | What is wrongly counted | Real example |
|---|---|---|
| W3 | Company facts still not covered by the excluded words: headcount, company age. | d2l.com about: "more than 1,000 employees"; geotab.com: "over 2,700 employees", "25+ Years of fleet management excellence"; close.com about: "We've spent 13+ years building the CRM" |
| W4 | Numbers that belong to someone else in the sentence. | klue.com security page: "supporting 500,000 organizations and more than 100 million users" (a founder's previous job) |
| W5 | Numbers that are part of a name. | klue.com /customers: "Co-founders, 10x GTM" counted as "10x" |
| W6 | A compliance standard held by a vendor, or a product capability phrased "... Compliant". | unbounce.com security page: "a Level 1 PCI-DSS compliant payment processor is used"; feroot.com: "Make Payment Webpages PCI DSS 4.0.1 Compliant" |
| W9 | Product dashboard numbers in a UI mock-up, when no "ago" is within 30 characters. | nulogy.com homepage: "Production Dashboard Real-time 98.4% Yield 1,247 Units/hr 2.1% Downtime"; "QA Breach Scrap Rate Exceeding 3% Tolerance" |
| W10 | Category or filter pages one level below the customers page counted as stories. | linear.app: /customers/saas, /customers/ai, /customers/fintech, /customers/consumer, /customers/hardware, /customers/healthcare, /customers/enterprise |
| W11 | Industry survey figures counted as proof (debatable: they are research, not customer results). | axonify.com: "87% of corporate leaders believe ...", "Only 56% of frontline employees agree"; nulogy.com: "200+ Respondents 78% Still use spreadsheets" |

Not counted as errors but worth knowing: a written story and a video of the
same customer are two pages and count as two stories (partnerstack.com
/customers/fiverr and /customers/fiverr-video).

## Display-only issues (do not change counts)

- The company part of an attribution can swallow the next capitalised word: close.com "Jem Millett, Director of Admissions @ Flatiron School See". The dedup key is the person's name, so the count is unaffected.
- Very long runs of text without punctuation count as one "sentence", so the excluded-word, standards, and question rules can reach a word far away (see M10).

## Scoring

- Each category reaches its maximum at 3 items, so sites with many case studies cluster at the top. The formula is deliberately unchanged. Number errors above rarely change a score, because most sites have well over 3 correct numbers.
