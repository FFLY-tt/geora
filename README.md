# geora

GEO (Generative Engine Optimization) visibility diagnostic and agent platform.

## Layout

Two backend services and two frontends, split so the working diagnostic
tool never has to be touched while the agent product is under development.
They share one repo and one `backend/.env` (same Perplexity/DeepSeek keys),
nothing else.

```
backend/
  shared/            # config, page fetching, text/report formatting rules —
                      # imported by both services, defined once
  diagnose_service/  # the free diagnostic tool (5 scoring engines) — live
  agent_service/      # decision / content / monitor agents — technical package, Answer Capsules, and Decide are live; the rest is still scaffold
frontend/
  diagnose/          # the free diagnostic tool's UI — live
  dashboard/          # agent product UI (post-paywall) — scaffold only
```

## Running locally

Diagnostic tool (the working product):

```
cd backend/diagnose_service && uvicorn server:app --reload --port 8000
cd frontend/diagnose && npm install && npm run dev   # http://localhost:5173
```

Agent service (`/api/health`, `/api/technical-package`, `/api/answer-capsules`, and `/api/decide` so far):

```
cd backend/agent_service && uvicorn server:app --reload --port 8001
cd frontend/dashboard && npm install && npm run dev   # http://localhost:5174
```

Both backend services read the same `backend/.env` — see
`backend/diagnose_service/requirements.txt` for dependencies (same set
for `agent_service` today).

## Batch diagnosis

`backend/scripts/batch_diagnose.py` runs the diagnostic tool's 6
scoring engines over a list of companies read from a CSV file, one
company at a time (never in parallel, with a 5-second pause between
companies), bypassing the 24-hour `/api/diagnose` result cache so
every company gets a fresh diagnosis.

```
cd backend/scripts && source ../venv/bin/activate   # same venv/env as the services above
python batch_diagnose.py companies_example.csv
python batch_diagnose.py companies_example.csv --yes   # skip the confirmation prompt
```

Input CSV columns: `company_name`, `domain` (see
`backend/scripts/companies_example.csv`).

Before confirming, the script prints an estimated upper bound on how
many Perplexity and DeepSeek calls the run will make. Before fetching
any site, it reads that site's `robots.txt` and skips the company
entirely if our User-Agent isn't allowed to fetch the homepage. The
User-Agent for the script's own direct requests includes a contact
email read from `CONTACT_EMAIL` in `backend/.env` (see
`backend/.env.example`) — optional, but recommended for a bulk run
against real third-party sites.

Output goes to `backend/scripts/output/` (gitignored):
- `results.jsonl` — one full diagnosis result per line, appended
  immediately after each company finishes.
- `summary.csv` — one headline row per company (scores, citation
  mention rate, top cited sources, status, error).

Re-running the script against the same output directory resumes where
it left off: any domain already present in `results.jsonl` is skipped.
