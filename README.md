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
