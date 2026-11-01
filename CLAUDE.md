# Working rules for this repo

## Branches and commits
- Start every new task from the latest main: run `git fetch origin`, then create a new branch
  from origin/main named `feature/<short-task-name>`. If the task depends on work that is not
  merged into main yet, stop and ask which branch to start from.
- After finishing a task and all tests pass, commit your changes and push the branch to origin.
  Do not wait to be asked.
- One task = one branch. Inside it, one commit per logical change.
- Commit message: short imperative English summary line, matching the existing git log style
  (for example "Add decide engine for ranking fixes"), then a blank line and 1 to 3 lines on why.
- Stage files by name. Never use "git add -A" or "git add .".
- Never commit: .env files, anything under backend/*/data/, venv/, node_modules/, __pycache__/.
- Never push to main. Never merge into main. Never force push. Never rewrite history.
  Never delete branches. The repo owner reviews and merges every branch personally.
- If tests fail, do not commit or push. Report the failure instead.
- After pushing, open a pull request into main with
  `gh pr create --base main --head <branch-name>`.
  Title: same style as the commit summary line.
  Body, in English: a short summary of what changed and why, the test results,
  and the list of changed files.
- Never merge pull requests. Never run `gh pr merge`. Never close or edit other people's
  pull requests. The repo owner merges every pull request personally.
- If `gh` is not installed or not logged in, do not try to install or log in yourself.
  Push the branch and give the compare link instead:
  https://github.com/FFLY-tt/geora/compare/main...<branch-name>
- In the final report, give the pull request link, the branch name, each commit hash and
  message, the files in each commit, and the CI result once it finishes.

## Scope
- Only touch the files the task explicitly allows. If the task seems to need more, stop and ask first.
- Do not refactor existing code unless asked. List anything you noticed in your final report instead.
- Code, comments and commit messages in English.

## Read first
- Before building anything a customer can see (the result page, the Content agent, Monitor, pricing,
  sales material), read docs/product_positioning.md.
- The owner's requirements are in docs/00_requirements.md. The target architecture is in
  docs/01_architecture.md and the reasons for each technical choice are in docs/02_decisions.md. Current
  progress and the next pull requests are in docs/03_progress.md. After a pull request is merged, update
  docs/03_progress.md.

## Customer-facing UI
- frontend/diagnose and frontend/dashboard are customer-facing.
  Never show internal details (method versions, cache, engine names,
  debug notes) in UI text. Keep them in the API response or logs.
- The reader is a marketer who is not technical. Every piece of text a visitor can see
  must be plain English with short sentences: headings, labels, buttons, hints, placeholders,
  error messages, empty states, chart text and the details. It applies to every frontend change.
  1. Simple words, short sentences, one idea per sentence. Write the way you would say it out loud.
  2. No brackets of any kind: ( ) [ ] { }.
  3. No dashes: no em dash, no en dash, and no hyphen used as a dash ("A - B").
     Write number ranges as "between 7% and 45%", never "7%-45%".
  4. No vertical bar |, and no slash used to separate things.
  5. No internal words: reliance, presence, gap, coverage, citation, unknown, layer, method,
     version, cache, engine, judgment, beta, confidence interval, Wilson.
     Say it in plain words instead, for example "source" for "citation".
  6. Titles of other websites' pages are cleaned before they are shown: the site name at the end
     is removed, brackets and what is inside them are removed, and the site name is shown on its
     own as "on example.com". See frontend/diagnose/src/utils/text.js.
  7. Text that is not ours (page titles, quotes found on a website) is wrapped in an element with
     data-external="true". It still may not contain the characters in rules 2 to 4, but it is not
     checked for the words in rule 5.
- The rules are checked by an automatic test, frontend/diagnose/src/pageText.test.js. It renders the
  real result page from saved real results, reads all visible text and fails when a rule is broken.
  Text written by the backend for customers is held to the same rules, see backend/shared/_text_format.py.
  Keep that test passing. Do not weaken it to make a change pass; change the wording instead.

## Production checklist
Every feature is built to production level (docs/00_requirements.md, R15). Check every pull request against
this list, and write each answer in the pull request description. "Not relevant" is an allowed answer when
it is true, with one sentence of why.
1. Many users at the same time: what happens when two people do this together? Shared data needs a
   database constraint or a lock, not a hope.
2. Doing it twice: is repeating the same action safe (idempotent)? A double click, a retry by the browser
   or a restart of a worker must not double the work, the cost or the data.
3. External services: does every call to another service have a timeout, a limited number of retries and a
   limit on how many run at once?
4. Local files: does the code write state to a file on the server? State belongs in the database.
5. Logs: is there a log line with the diagnosis id for each important step, so one diagnosis can be traced?
6. Tests: are there tests, including the error cases (the service is down, the input is bad, two things
   run at once)?
7. Cost: can this make the API bill grow without a limit? Is there a cap, and are the calls counted?

## Working method
- Write the plan before the work. Say what will change, what will not, and how it will be checked.
- Every task list has verification steps. A task without a way to check it is not finished.
- Change only the code the task is about. Write down what else you noticed, in the report, and do not fix it.
- Done means the tests pass. Never say "done" for a change whose tests were not run.

## Language
- Always reply to the repo owner in Simplified Chinese: explanations, questions, progress updates,
  and final reports.
- Keep these in English: code, code comments, commit messages, branch names, file contents,
  and anything written into the repository.
- Keep technical terms, file names, function names and command output as they are, without translating them.
