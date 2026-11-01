# Progress

Updated after every merged pull request. Last update: 2026-10-31, after pull request #24 was merged and
the architecture plan (this pull request) was written.

## Where we are

The free diagnostic tool works end to end on one machine. It is a prototype in how it runs, and a solid
product in what it measures. The architecture work in `01_architecture.md` is about the first part.

### Done

| Area | State |
|---|---|
| Question sets | 5 categories, 15 neutral buying questions each, frozen, version 4. No product, company or brand names. Created and refreshed by `scripts/question_sets.py`, checked by rules and an LLM review. |
| Citation visibility | 15 different questions asked once each (method v4). Mention rate with a Wilson interval. The shared mention rule is `classify_mention`, also used for the optional competitor counts. |
| Evidence density | Rule based, no LLM (v3.2). Known limits in `docs/evidence_rules_known_limits.md`. |
| Topic relevance, content recency, technical readability | Working. Judgments are cached by input hash. |
| Fact consistency | Five fixed fields, 3 runs and a vote, official text with spaces kept between elements. |
| Layered diagnosis | Sources grouped by kind of website (rules, no LLM), pages checked live. Known limits in `docs/source_layers_known_limits.md`. |
| Result page | Headline, range, optional competitor list, two bar blocks ("Where AI looked for answers", "Is your website ready for AI?"), "Show details" with an email box. |
| Customer text rules | In `CLAUDE.md`. Checked by `frontend/diagnose/src/pageText.test.js` on four saved real results. Backend written text is held to the same rules. |
| Reproducibility | Frozen question sets, versioned methods, stored judgments, provenance in every result, `scripts/compare_runs.py`. |
| Tests and CI | 384 backend tests and 104 front end tests, all offline. CI runs them and builds both front ends. |

### Merged pull requests that shaped the current state

#21 fact consensus, #22 customer facing copy, #23 source layer diagnosis, #24 final diagnose (15 questions,
competitors, buying questions without product names, plain text rules, the two bar blocks).

## Known issues

1. **The service is not production ready.** 38 gaps are listed in `01_architecture.md`, section 14: 16 high,
   15 medium, 7 low. The most important are local state files, a request that waits minutes, no retries and
   no limits on external calls, a server side request forgery risk, and no cost cap.
2. **`docs/product_positioning.md` is missing.** It was not in the repository or in the working folders on
   2026-10-31. `CLAUDE.md` already tells everyone to read it before customer facing work. The owner needs to
   add the file.
3. **`docs/source_layers_known_limits.md` is out of date.** It says each question is asked three times and
   talks about five independent samples. The method now asks 15 different questions once.
4. **No PostgreSQL on the owner's machine.** `psql --version` gave `command not found` on 2026-10-31. A
   container or a local install is needed for development (see P3).
5. **A new category is created while a diagnosis runs.** This writes configuration at run time and will not
   be allowed in production. See `01_architecture.md`, open question 1.
6. **`topic_relevance` judges 15 questions one after another.** A fresh run takes a minute or more longer
   because of that.
7. **Unused code.** `frontend/diagnose/src/utils/provenance.js` has no user now.
8. **`KNOWN_PRODUCT_NAMES` is written by hand** and will miss names. It is our own judgment and easy to extend.
9. **Pages that could not be opened.** Some third party sites block our requests. These pages are left out of
   the bars, which is correct but means the bars can show fewer pages than AI read.

## Next steps

Nine small pull requests, in this order. Each one can be tested on its own and merged on its own, and each
leaves the product working. Every pull request description answers the production checklist from
`CLAUDE.md` and lists which gaps (G numbers in `01_architecture.md`, section 14) it closes.

Order reasons: P1 and P2 need no database and remove the biggest risks early. P3 to P5 move state into
PostgreSQL step by step, results first because they are the simplest. P6 and P7 change how a diagnosis runs
and how the page asks for it. P8 adds cost control. P9 brings the scripts and the agent service along. P10
packages it.

### P1. Settings and input safety

- **Does.** Settings per provider (timeouts, limits) read from environment variables. Checks for the domain
  (format, length), the company name (length) and the email (format). Allowed origins for CORS from settings.
  Update `docs/source_layers_known_limits.md`.
- **Does not.** No database, no new endpoints, no behavior change for valid input.
- **Closes.** G26, G35, G39.
- **Accept when.** Bad input gets a clear `422` with a plain message; valid input behaves as before; the
  allowed origins come from the environment and the defaults are safe; all existing tests pass; new tests
  for each input rule, including the empty and the very long case.

### P2. One client for external calls, and diagnosis logging

- **Does.** A module in `shared/` for Perplexity, DeepSeek and page fetches: a limit per provider,
  separate connect and read timeouts, a total time limit, retries with growing waits for timeouts, `429` and
  `5xx` (respecting `Retry-After`), a safety check for page addresses (public IPs only, redirect limit,
  body size limit), and a hook that records each call. A diagnosis id in a context variable and JSON logs.
  All nine places that call out use the client.
- **Does not.** No database (the call hook writes to the log only), no change to scores or text.
- **Closes.** G13, G14, G15, G24, and the hook of G17.
- **Accept when.** A fake server shows: a `429` then success is retried and returns the answer; a timeout is
  retried then reported; a private address, `localhost` and a redirect to a private address are refused;
  a huge body is cut off; the limit per provider holds when 50 calls are started at once; logs of one
  diagnosis can be found by its id; results of the existing tests do not change.

### P3. PostgreSQL foundation, results and leads

- **Does.** SQLAlchemy 2 and Alembic set up. Tables `diagnosis_results`, `leads`, `api_call_log`. The 24 hour
  result cache and the leads store move to PostgreSQL. A development database with Docker Compose. CI gets a
  PostgreSQL service and a migration check. `DATABASE_URL` in settings. Remove `result_cache.py`,
  `leads_store.py` and their `data/` folder use.
- **Does not.** No queue, no new API shape, no change to judgments or question sets.
- **Closes.** G1, G4, G7, G9, G28, and the storage half of G17.
- **Accept when.** The same request gives the same response as before; a result survives a restart; two
  processes see the same result; saving the same result twice makes one row; invalid email is refused;
  migrations apply to an empty database and roll back; CI runs against PostgreSQL; tests cover a database that
  is down (a plain error, not a crash).

### P4. Judgments, categories and question sets

- **Does.** The judgment cache and the company to category assignments move to PostgreSQL. Question sets are
  read only from git; the service never writes them. A company with no known category ends with a plain
  status. `scripts/question_sets.py` stays the only way to add or change a set and gets a command to register a
  new category.
- **Does not.** No change to the questions or to any score. No raw data tables yet.
- **Closes.** G2, G3, G6, G21, G33.
- **Accept when.** No code under `diagnose_service` writes to `state/` (a test searches for it); two companies
  of one new category at the same moment cannot create two categories; a stored judgment is reused; a new
  judgment is written once; an unknown category gives the plain status and no API spend; all question set
  tests pass.

### P5. Raw data, shared industry answers, shared page cache

- **Does.** Tables `raw_ai_responses`, `raw_pages`, `industry_answers`, `page_cache`. The 15 answers of a
  category are fetched once per UTC day and shared. Cited pages are stored and shared for 7 days. Each
  company is checked against the stored data. Raw data is inserted only. Add a script that recalculates a
  result from stored data.
- **Does not.** No queue, no new API shape. The answer day bucket and the 7 days are settings.
- **Closes.** G16, G20, G30, and half of G29.
- **Accept when.** Two companies in one category on one day make 15 Perplexity calls in total; a page cited
  for two companies is fetched once; recalculating from stored data gives the same result as the live run
  (a test compares them); rows in the raw tables cannot be updated (a test tries); the numbers of a
  diagnosis do not change because of caching except where the cache time says so.

### P6. Job queue, worker and the new API

- **Does.** `diagnosis_jobs` and the worker with `FOR UPDATE SKIP LOCKED`, heartbeat and retry. Steps with
  stored output. The job key and sharing of a running job. `POST /api/diagnoses` returns an id and
  `GET /api/diagnoses/{id}` returns the status, a progress label and the result. Competitors are applied per
  request. The old `POST /api/diagnose` stays for one release and is marked as old.
- **Does not.** No front end change, no abuse limits yet.
- **Closes.** G8, G10, G11, G19, G22, G23, G25, G38. (G37, the hand written product name list, stays as it is.)
- **Accept when.** Two identical requests at once make one job and one set of API calls; a worker killed in
  the middle of a job is replaced and the job finishes without repeating finished steps; a job that fails 3
  times ends as `failed` with a plain reason; 20 jobs started at once all finish and respect the provider
  limits; the web service answers while jobs run; tests for each of these.

### P7. Front end polling

- **Does.** The page starts the diagnosis, then asks for status every 2 to 3 seconds and shows a plain
  progress line. The API address comes from build settings. Errors and a lost connection get plain messages.
  Remove the unused `provenance.js`.
- **Does not.** No change to the result page, no change to the text rules.
- **Closes.** G12, G36.
- **Accept when.** A page reload during a diagnosis picks it up again from the id; progress text follows the
  customer text rules and the page text test covers it; a failed job and a network error show plain
  messages; polling stops when the page is closed; the front end tests and build pass.

### P8. Cost control and abuse protection

- **Does.** Cloudflare Turnstile on the form and verified by the service. Limits per IP and per email per
  day, a daily cap on diagnoses, caps per provider, all in `usage_counters`. A cost report from
  `api_call_log` (per diagnosis and per day). Plain "we are busy" message at the cap.
- **Does not.** No login, no payment.
- **Closes.** G17 (report), G18.
- **Accept when.** A request without a valid token is refused with no job and no spend; the 11th request from
  one IP in a day is refused; at the daily cap nothing is queued; the cost report adds up to the
  `api_call_log` rows; limits can be changed by settings without a deploy of code; tests for each limit.
  The values need the owner's budget (open question 4).

### P9. Scripts and the agent service

- **Does.** One shared function `run_diagnosis` used by the worker, `batch_diagnose.py` and
  `stability_audit.py`, with a switch for no database (an in memory repository). The audit bypasses the cache
  through the repository, not by patching names. The batch output has the same shape as the live result.
  `agent_service/_cache_store.py` moves to PostgreSQL.
- **Does not.** No change to what the scripts measure.
- **Closes.** G5, G31, G32, the rest of G29.
- **Accept when.** `batch_diagnose` output equals the live response for the same input; the stability audit
  runs without patching engine names; the agent service passes its tests on PostgreSQL; no file state is left
  in either service.

### P10. Packaging and deployment files

- **Does.** Dockerfiles for the web service and the worker, a health endpoint that checks the database,
  settings documented in `.env.example`, a short runbook (start, migrate, roll back, read the logs, read the
  cost report). A CI job that builds both images.
- **Does not.** No choice of hosting platform.
- **Closes.** The deployment part of `01_architecture.md`, section 12.
- **Accept when.** Both images build in CI; a container started from each image runs against a test
  PostgreSQL; the health endpoint fails when the database is down; the runbook steps work on a clean machine.

### Not planned yet

Billing, accounts, the paid Content agent and Monitor, and the sales material are outside this plan. Read
`docs/product_positioning.md` before planning them.
