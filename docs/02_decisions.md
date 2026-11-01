# Technical decisions

One entry per decision. Each entry has: the decision, the reason, what was considered and not chosen,
and when to think again. Decisions are written on 2026-10-31 unless another date is given. New decisions
are added at the end. A decision that is replaced is not deleted: a new entry says which one it replaces.

Labels: **[ASSUMPTION]** is something we guessed and have not measured.

## D1. PostgreSQL for all state, instead of DuckDB or local JSON files

**Decision.** All state lives in PostgreSQL: jobs, results, caches, raw data, emails, usage counts.
The servers keep no state on disk.

**Reason.**
- Many requests write at the same time. PostgreSQL handles concurrent writers, transactions and unique
  constraints. These are what remove the problems G1, G4, G19 and G21 in `01_architecture.md`.
- Managed PostgreSQL has backups, which personal data (emails) and paid raw data need.
- The owner requires PostgreSQL (`00_requirements.md`, R16).
- The queue, the results and the caches can change in one transaction.

**Considered and not chosen.**
- Local JSON files (today): no safe concurrent writes, lost when a container restarts, the whole file is
  read for each lookup.
- DuckDB: very good for analysis of a file by one process. It is not built for many writers from several
  processes, and managed hosting for it is limited.
- SQLite: the same one writer limit, and the file lives on one machine.
- A document database: our data is relational (jobs, results, raw data, counters) and needs constraints.

**Think again when.** Writes per second or data size are beyond what one managed PostgreSQL handles
comfortably (not expected at the sizes in `01_architecture.md`, section 2 [ASSUMPTION]).

## D2. A PostgreSQL queue, instead of Redis and Celery

**Decision.** Jobs are rows in `diagnosis_jobs`. Workers take them with
`SELECT ... FOR UPDATE SKIP LOCKED`.

**Reason.**
- At 300 jobs a day the load is tiny for PostgreSQL.
- One service fewer to run, pay for, secure and monitor.
- A job and its data change in one transaction, so a job is never "done" without its result.
- Easy to inspect with SQL.

**Considered and not chosen.**
- Redis with a queue library: needs another service and a second place where state can be lost or
  disagree with the database.
- Celery: a large tool with its own broker, serialization and retry model. We need about 100 lines of
  queue code, and we need our own step and heartbeat rules anyway.
- A cloud queue service: ties us to one platform.

**Think again when.** Jobs per second grow into the dozens, we need delays and priorities across many
queues, or workers spend noticeable time waiting on the lock. Then move the queue to Redis. The job table
stays as the record.

## D3. Polling instead of WebSocket

**Decision.** The browser asks `GET /api/diagnoses/{id}` every 2 to 3 seconds.

**Reason.**
- A diagnosis takes 1 to 3 minutes. A 2 to 3 second delay in showing progress does not matter.
- Polling is stateless: any web process can answer, a reload of the page just asks again, and it works
  through every proxy.
- WebSocket needs sticky connections or a message bus between processes, and reconnect logic.

**Considered and not chosen.** WebSocket, server sent events: more moving parts for no real gain at this
duration.

**Think again when.** We show live detailed progress (for example every page as it is read), or polling
load becomes a real cost. At 20 users polling every 2.5 seconds that is 8 cheap reads a second.

## D4. Keep FastAPI and React

**Decision.** The web service stays FastAPI with Pydantic. The front end stays React with Vite.

**Reason.** They work, they have tests, and the owner knows them. Nothing in the target design needs a
different framework. Replacing them would cost time and add no value to the goals in section 1.

**Considered and not chosen.** Django (more than we need, and a rewrite), Next.js (server rendering is not
needed for a tool behind a form).

**Think again when.** We need server side rendering for search engine traffic on the result pages.

## D5. SQLAlchemy 2 and Alembic

**Decision.** Database code uses SQLAlchemy 2 (Core and typed ORM models) with the `psycopg` 3 driver.
Schema changes are Alembic migrations kept in git and run as a separate deploy step.

**Reason.**
- Schema changes are reviewed in pull requests and are repeatable on every machine and in CI.
- SQLAlchemy 2 is the standard tool, with good typing and PostgreSQL features (`FOR UPDATE SKIP LOCKED`,
  upserts, JSONB).
- The same models serve the web service, the worker and the scripts.

**Considered and not chosen.** Hand written SQL files and a migration script (we would rebuild Alembic
badly), an ORM with its own migrations tied to a framework we do not use, raw `psycopg` everywhere (no
shared models).

**Think again when.** The ORM gets in the way of a hot query. Then use Core or plain SQL for that query.

## D6. Industry answers are shared between companies

**Decision.** The 15 Perplexity answers for one category on one UTC day are fetched once and stored. Every
company in that category is checked against the same stored answers.

**Reason.**
- The question sets contain no company name (see the product name rule in `04_interview_qa.md`), so the answers do
  not depend on the company. Fetching them again for each company wastes money.
- It cuts Perplexity calls from 15 per diagnosis to 15 per category per day. This is the main saving,
  because the API cost is the bottleneck.
- Two companies in one category are then measured on identical answers, which makes a comparison fair.
- It gives reproducibility: the answers are stored, so the numbers can be recalculated.

**Considered and not chosen.**
- Fetch for every company (today): costs more and gives two companies different samples of a noisy source.
- A longer sharing time than a day: answers would age and hide real change in how AI answers.

**Think again when.** A category has many companies with one company asking for a fresh run. Or if the
questions start to contain company specific words (they must not, see the product name rule).
Also if the owner prefers a rolling 24 hours instead of a UTC day (open question 3).

## D7. Cloudflare Turnstile against abuse

**Decision.** A Turnstile token is required to start a diagnosis and is verified by the web service.
It comes together with limits per IP and a daily cap.

**Reason.** The free diagnosis spends real money for anyone who calls it. A human check stops simple scripts
at no cost to honest users and without a login. Turnstile is free and is mostly invisible to the user.

**Considered and not chosen.**
- Only an IP limit: easy to get around, and many honest users share an IP.
- A login before the first diagnosis: loses users at the most important step.
- reCAPTCHA: more intrusive for users and has data privacy concerns.
- An email check first: later, as an extra limit (open question 5).

**Think again when.** Bots get through Turnstile, or we add accounts. Then a stricter plan or login.

## D8. A managed database

**Decision.** Use a managed PostgreSQL service. The platform is chosen later.

**Reason.** Backups, security updates, failover and monitoring are the job of the service, not of a team of
one. Personal data and raw data must not be lost. The cost of a small managed instance is small next to
the API cost.

**Considered and not chosen.** Running PostgreSQL ourselves on a server or in a container: cheaper on paper
and costs time and risk the first time a disk fills or a backup is missing.

**Think again when.** Cost of the managed service grows beyond the cost of running it ourselves, or a data
location rule requires it.

## D9. Question sets and rule lists stay in git, read only at run time

**Decision.** Question sets and the lists of rules are versioned files in git. The services read them and
never write them. Companies' categories are data and go to PostgreSQL.

**Reason.** A question set is the test itself. It must be reviewed, versioned and the same everywhere
(R4, R5). A change goes through a pull request. Writing it at run time (today) made two machines able to
have different tests.

**Considered and not chosen.** Question sets in the database: easy to change at run time and hard to
review, and two environments could drift. Generating at run time: see G3.

**Think again when.** We have so many categories that a pull request for each is too slow. Then an admin
tool that writes a reviewed draft into git.

## D10. One shared client for external calls

**Decision.** Every call to Perplexity, DeepSeek and web pages goes through one module in `shared/` with
limits per provider, timeouts, retries, a page safety check and a call log.

**Reason.** Fixing timeouts, retries and limits separately in nine places is how problems come back
(R17). One place gives one set of tests and one place to count cost.

**Considered and not chosen.** A retry decorator on each function (the limits would still be per request);
an HTTP library with its own retry only (no limits, no log).

**Think again when.** We use several workers and need a limiter shared between processes (then it moves
to PostgreSQL).

## D11. Raw data is kept, append only

**Decision.** Raw AI replies and raw page bodies are stored compressed in PostgreSQL and never updated.

**Reason.** A result can be recalculated and explained later, and a rule change can be tested against real
stored data without new API calls. It is the base for R4.

**Considered and not chosen.** Keep only the final result (today): the cost of an API call is lost and the
result cannot be checked later. Store raw data as files: state on a server disk.

**Think again when.** Raw data size becomes a cost [ASSUMPTION]. Then move bodies to object storage and
keep the pointer.
