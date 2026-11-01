# Interview questions and answers

For each finished stage: the questions an interviewer may ask, and answers based on what is really in this
repository. Answers say what we did and why, and where we are not sure. They do not claim more than the
code does. New stages are added after they are merged.

---

## 1. Reproducibility design

**Q: How do you make sure the same input gives the same result?**
A: First I find where the variation comes from. In this project there were three sources: the test itself
changing, the language model judging the same text differently each time, and the world changing. I removed
the first two and recorded the third.
- The test is frozen. Each product category has one question set, stored in git with a version number. It
  never expires and is never regenerated on its own, so every machine asks the same questions.
- Model judgments are cached by a hash of the exact input (the page text, the prompt version, the model
  name). The same text gets the same judgment, with no new model call.
- Methods are versioned. Every result records the method versions and the question set version, and the
  page only compares results made with the same versions.
- The world changes, because Perplexity does not give the same answer twice. That cannot be frozen, so we
  measure it honestly: a Wilson interval on the mention rate, and the result is kept 24 hours so a repeat
  visit does not show random change.

**Q: How did you find the problem in the first place?**
A: A stability audit script ran the same company several times with the cache switched off and compared
every dimension. Scores for the same page text moved, which showed that the judgment itself was random.
That led to the judgment cache and later to replacing a model judged dimension with code rules.

**Q: What is still not reproducible?**
A: The AI answers themselves. Today they are fetched live. The architecture plan stores them (append only,
shared per category per day), so a result can be recalculated from stored data. Until then, I say that the
same input gives the same result only when the stored data is the same.

---

## 2. Evidence rules engine

**Q: Why did you replace the model judging evidence with code rules?**
A: The model gave a different score for byte for byte identical text. Caching hid it but did not explain
it. A score a customer cannot repeat is not a score. Rules are repeatable, testable, and can show the user
the exact item and the page it came from.

**Q: What do the rules look for?**
A: Specific numbers, named quotes, customer story pages and third party ratings or certifications. Each item
is shown with its original words and its source page, so the user can check it.

**Q: What was the hardest design decision?**
A: Precision over recall. A wrong item raises the score and misleads the user. A missed item only makes the
score cautious. Later I replaced "named customers found from logo images" with "customer story pages". On a
holdout set only about 60 percent of logo items were real customers, and an AI assistant cannot read logo
images at all. That change is written down with the reason in `docs/evidence_rules_known_limits.md`.

**Q: How did you test the rules without fooling yourself?**
A: A development set of sites to write the rules, and holdout sets that were only used to check, never to
change the rules afterwards. Known failures are listed with real examples, not hidden.

---

## 3. Layered diagnosis by kind of website

**Q: Why not just analyse the customer's website?**
A: AI answers a question from many sites, and the company's own site is often a small part. If you only
look at the company's pages you explain little of why AI does or does not mention it. So we group the
sources AI used by kind (review sites, community, best tools lists, guides, the company's own site) and
check where the company shows up.

**Q: How do you decide the kind of a site?**
A: By rules: lists of domains and words in the address or title. The lists are our own judgment and are
versioned. A site on no list is never guessed; it is "other companies". No model is used, so the result is
the same every time.

**Q: How do you know a page mentions the company?**
A: The page is fetched and read. A whole word match on the name or the domain counts. A name that is also an
ordinary word (such as "Close") counts only as a heading or in a stricter pattern, because a loose rule made
9 of 10 false mentions on close.com. Pages that are bot checks are "could not open", not "does not mention".

**Q: Why did the page change from a radar chart to bars?**
A: A radar chart needs a value on every axis, and the data had empty axes for small companies, so it showed
nothing. Bars can show only the kinds that have data, and length is easy to compare. Counting pages ("1 of
29") is also easier to picture than a share of citations.

---

## 4. Question set design and removing product names

**Q: How are the test questions made, and how do you know they are good?**
A: Each category has 15 questions, asked once each. They are generated from a description of the category
only, never from the company's own homepage, so the wording is not copied from a site. Then each question
passes two checks that must both agree: rules in code, and a model review. A batch that fails is not
accepted whole: questions that passed are kept and only the failed ones are replaced.

**Q: Why were the first questions a problem?**
A: They were "how do I" questions. AI answers those with a tutorial and rarely names a product, so the
numbers did not measure what we care about, which is whether AI recommends the company when someone asks
what to buy. We changed every question to a neutral "which product" question and wrote a rule that blocks
"how to" and "what is" questions.

**Q: Why did you remove product names from the questions?**
A: A question like "Alternatives to Salesforce" leads the answer: AI will name Salesforce. That raised that
product's count in the competitor list, and if we later test that product itself, its own mention rate would
be too high. This is a leading question. The fix: no product, company or brand names at all, checked by a
list of known names plus the tested companies' names and domains, and also by the model review. Two
layers is deliberate, because the model can miss.

**Q: What is the weak point of that check?**
A: The list of product names is written by hand and will always miss some. It is our own judgment and is
easy to extend when a miss is found.

---

## 5. Confidence intervals and independent samples

**Q: Why do you show "likely between 15% and 58%" and not only 33%?**
A: With 15 answers one number looks exact and is not. The Wilson interval gives the range the true rate is
likely in. It works with small samples and does not go below 0 or above 100, unlike the simple plus and minus
formula. Two results whose ranges overlap are not called a rise or a drop.

**Q: What was wrong with 5 questions asked 3 times?**
A: The three repeats of one question usually gave the same sources, so 15 answers were about 5 independent
samples. The interval on 15 answers was too narrow, and the number jumped when one question moved. Asking 15
different questions once costs the same 15 calls and gives more independent information, so the number is
steadier.

**Q: How do you explain independent samples to a non technical person?**
A: Asking the same person the same question three times is not three opinions. Asking three different
people is.

---

## 6. Automatic check of the text customers see

**Q: How do you keep the interface text simple?**
A: Write the rules down, then write a test that enforces them. The rules are in `CLAUDE.md`: short plain
sentences, no brackets, no dashes, no vertical bars, no internal words. The test renders the real result page
from four saved real results, reads every visible word, and fails if a rule is broken. It also compares the
text above the "Show details" button with exactly the allowed text. Nobody has to remember the rules.

**Q: How do you handle text that is not yours, like page titles from other sites?**
A: Titles are cleaned before display (site name at the end removed, brackets removed). Text that is not ours
is marked, so the test still checks it for forbidden characters but does not apply the internal word rule
to it.

**Q: What did the test catch?**
A: The first run found that text written by the model contained the word "gap", our internal word. So the
writing prompt now lists the internal words, and the check regenerates the text when one appears.

---

## 7. The architecture design (this pull request)

**Q: How do you plan a move from prototype to production?**
A: Estimate the load, find the real bottleneck, write the target design and each decision with its reason,
compare the existing code to it, and split the gap into small steps that can be tested and released alone,
ordered by risk and dependency. Here the load estimate said the bottleneck is the cost of external APIs,
not servers. That decided what to build first: limits, retries and sharing, not scaling servers.

**Q: Why PostgreSQL as the queue and not Redis?**
A: At a few hundred jobs a day PostgreSQL is enough, and a second service is more to run and to keep in step
with the database. `SELECT ... FOR UPDATE SKIP LOCKED` lets many workers take different jobs safely, and the
job and its data change in one transaction. When the load grows we can move the queue to Redis. The job table
stays.

**Q: Why share the AI answers between companies?**
A: The questions have no company name, so the answers do not depend on the company. Fetching 15 answers once
per category per day instead of once per diagnosis cuts the main cost, and puts companies in one category on
the same data, so comparing them is fair.

**Q: What was the most serious problem you found in the current code?**
A: The server fetches whatever address the user types, with no check that it is a public address. A user could
point it at an internal address. That is a security hole and is the first thing the shared call module fixes.
The next ones: no retry on a failed call, so one hiccup silently changes a result; no limit on total calls, so
a script could spend the API budget in a day; and state kept in local files, which disappears when a
container restarts.

**Q: What would you do first?**
A: The shared external call module (timeouts, retries, per provider limits, address checks, logging with a
diagnosis id), because it needs no database and removes the biggest risks. Then move state into PostgreSQL
step by step, then the job queue.

**Q: What are you unsure about?**
A: The load numbers are assumptions. I would measure the first month and adjust the limits. Also the
cache time of the company's own pages is an open question: 7 days would hide a customer's fix, so I propose
24 hours.
