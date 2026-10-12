# evidence_density rules: known limits (v3.1-rules)

`backend/diagnose_service/engines/evidence_engine.py` scores evidence with
deterministic code rules. The rules favour precision over recall: a wrong
item inflates the score and misleads the user, a missed item only makes the
score conservative. This file lists what the current version is known to
miss or get wrong, each with a real example seen on a live site in October
2026. Examples from close.com, feroot.com, linear.app, freshbooks.com,
clio.com and vidyard.com were used while writing the rules (development
set). Examples from jobber.com, hootsuite.com, klue.com, unbounce.com,
waveapps.com and tophat.com were only used to check them (holdout set); the
rules were not changed after looking at those.

## Misses (evidence on the page that is not counted)

| # | What is missed | Real example |
|---|---|---|
| M1 | Reviews attributed only to a job title, with no person or company name (typical G2 review snippets). | feroot.com homepage: "... is now business as usual. Chief Security Officer Verified G2 review 5 / 5" |
| M2 | Attributions without the comma or "@" / "at" the rules expect, for example name and title run together after line breaks were collapsed. | klue.com homepage: "Jordan Glenn Sr. Business Process Analyst Case Study 72% Seller Adoption" |
| M3 | Customer lists introduced by wording other than "trusted by" / "customers include / like / such as". | feroot.com homepage: "Security teams at Instacart, Reddit, and Gusto use Feroot to monitor every third-party script" |
| M4 | A logo or list entry holding several names, or a company name with "&", "and" or "/". The whole entry is dropped rather than guessed apart. | vidyard.com trust page: "Vidyard is reviewed and trusted by Google Slack Zoom Gong Microsoft Salesloft Theta Lake" |
| M5 | Customer logos the rules cannot read: no alt text, SVG or CSS images, or a logo strip whose container gives no customer / trusted signal. The cause was not checked one by one. | klue.com: 0 named customers found on the homepage and /customers, although /customers is a case study page. |
| M6 | Key pages the homepage does not link to with a recognisable path. | freshbooks.com: no customers / case studies page was found from the homepage links, so its customer stories were never read. |
| M7 | Any site that refuses our request. The score is -1 with the reason, not a guess. | clio.com: homepage returns "blocked: HTTP 403". |
| M8 | Numbers whose value is filled in by JavaScript after the page loads; only the raw HTML is read. | tophat.com homepage shows "0% say Top Hat is easy to use" in the raw HTML, most likely an animated counter that starts at 0 (not verified). The real value is missed and a wrong 0% is counted, see W7. |

## Wrong finds (counted, but not real evidence)

| # | What is wrongly counted | Real example |
|---|---|---|
| W1 | Image alt texts inside a section that mentions "customers": avatars, illustrations, feature icons, UI screenshots. This was the most common error on the holdout set. | jobber.com: "Stacy Flanagan avatar", "A man holding a cell phone", "A quote in the Jobber app"; close.com: "Slack message from a Close customer"; waveapps.com: "Invoicing", "Wallet"; freshbooks.com: "Chevron Up"; hootsuite.com: "rocketship"; unbounce.com: "Ecommerce stores" |
| W2 | A review site badge in a logo strip counted as a named customer. | hootsuite.com homepage logo strip: "G2" |
| W3 | Company facts that are numbers but not proof of customer results: funding, grants, team size, years of experience. | klue.com about: "Over $81M Raised", "founded by 15+ year industry veterans"; jobber.com: "Our $150,000 grant program", "$250K Available in 2026"; close.com about: "We've spent 13+ years building the CRM" |
| W4 | Numbers that belong to someone else in the sentence. | klue.com security page: "he worked on data security infrastructure supporting 500,000 organizations and more than 100 million users" (a founder's previous job) |
| W5 | Numbers that are part of a name. | klue.com /customers: "Co-founders, 10x GTM" counted as "10x" |
| W6 | A compliance standard held by a vendor or asked about in an FAQ, or a product capability phrased "... Compliant". The "certified / compliant / report" sentence rule cannot tell who holds it. | unbounce.com security page: "a Level 1 PCI-DSS compliant payment processor is used"; unbounce.com pricing FAQ: "Is Unbounce GDPR compliant?"; feroot.com homepage: "Make Payment Webpages PCI DSS 4.0.1 Compliant" |
| W7 | Placeholder numbers from JavaScript counters (see M8). | tophat.com: "0% say Top Hat is easy to use" |
| W8 | One person counted twice when two pages give their title differently: the name rule takes up to three capitalised words, so "Senior" becomes part of the name. | close.com: "Jem Millett, Director of Admissions @ Flatiron School" on the homepage and "Jem Millett Senior Director of Admissions, Flatiron School" on /about |

## Display-only issues (do not change counts)

- The company part of an attribution can swallow the next capitalised word, because whitespace-collapsed text has no line breaks: close.com "Jem Millett, Director of Admissions @ Flatiron School See", "Michael Fomkin, CEO & Founder @ VIP Ignite Try". The dedup key is the person's name, so the count is unaffected.
- Very long runs of text without punctuation count as one "sentence". The excluded-word rule and the standards rule work per sentence, so on such pages they can exclude a real stat, or let a standard through, because of a word far away.

## Scoring

- Each category reaches its maximum at 3 items, so sites with many case studies (close.com, linear.app, vidyard.com) cluster at the top and the score separates them poorly. The formula was deliberately left unchanged in this version.
