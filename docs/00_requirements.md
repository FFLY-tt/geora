# Requirements from the repo owner

This file records every requirement the repo owner has given for this project.

Rules for this file:
- Append only. Never delete or rewrite an entry. If a requirement changes, add a new entry that says
  which earlier entry it replaces.
- Every entry has the date it was recorded. Requirements the owner gave before this file existed were
  written down on 2026-10-31, the date this file was created; the owner is the source of all of them.
- Entries are short. Details and reasons belong in `01_architecture.md` and `02_decisions.md`.

Product positioning is in `docs/product_positioning.md`. Read it before building anything a customer
can see (see `CLAUDE.md`).

## Truth and evidence

| # | Date | Requirement |
|---|---|---|
| R1 | 2026-10-31 | Never make up data. Content that has not been verified is not a fact. Judgments and research conclusions are labeled as such and kept apart from facts. |
| R2 | 2026-10-31 | When a rule, a list or a threshold is our own judgment, the code comment and the docs say so (for example the lines between Good and Needs work, the list of known product names). |
| R3 | 2026-10-31 | Report results as they are. Do not tune questions, rules or numbers to make a result look better. |

## Reproducibility

| # | Date | Requirement |
|---|---|---|
| R4 | 2026-10-31 | The same input, run on any computer at any time, must give the same result. Frozen question sets, versioned methods and stored judgments exist for this reason. |
| R5 | 2026-10-31 | A result says what it was made from: method versions and question set version. Results are only compared when those match. |

## Language and communication

| # | Date | Requirement |
|---|---|---|
| R6 | 2026-10-31 | Code, comments, commit messages, branch names, docs and anything else written into the repo are in English. Replies to the owner are in Simplified Chinese. |
| R7 | 2026-10-31 | Every prompt that is written for the owner ends with a section "What you should understand this time": the key idea behind the task and an interview question with its answer. |
| R8 | 2026-10-31 | Final reports to the owner give the pull request link, branch, each commit hash and message, files per commit, and the CI result. |

## Git and review

| # | Date | Requirement |
|---|---|---|
| R9 | 2026-10-31 | Claude Code may commit, push and open pull requests. It never merges, never pushes to main, never force pushes and never rewrites history. The owner merges every pull request. |
| R10 | 2026-10-31 | One task is one branch. One commit per logical change. Stage files by name. If tests fail, do not commit or push. |
| R11 | 2026-10-31 | Only touch the files a task allows. Things noticed along the way are listed in the report, not fixed. |

## Secrets

| # | Date | Requirement |
|---|---|---|
| R12 | 2026-10-31 | Never print the contents of `.env`. Never commit `.env`. Real keys and passwords never appear in the repo, in logs or in chat. `.env.example` holds fake values only. |

## Customer-facing UI

| # | Date | Requirement |
|---|---|---|
| R13 | 2026-10-31 | The front end is for customers. Plain English, short sentences. No brackets, no dashes, no vertical bars, no internal words. The rules are in `CLAUDE.md` and are checked by an automatic test. |
| R14 | 2026-10-31 | Never show internal details (method versions, cache, engine names, debug notes) on the page. They stay in the API response or in logs. |

## Production quality

| # | Date | Requirement |
|---|---|---|
| R15 | 2026-10-31 | Build every feature to production level. Think about many users at the same time. Do not build a demo that only works for one person. |
| R16 | 2026-10-31 | The database is PostgreSQL. |
| R17 | 2026-10-31 | Solve the problem at the design level every time. Do not patch the same problem again and again. |
| R18 | 2026-10-31 | Every pull request is checked against the production checklist in `CLAUDE.md`, and the result is written in the pull request description. |

## Method

| # | Date | Requirement |
|---|---|---|
| R19 | 2026-10-31 | Write a plan before the work. Every task list has verification steps. Done means the tests pass. |
| R20 | 2026-10-31 | Do not add features that were not asked for. When asked for "only this change", change only that. |
