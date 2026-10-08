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

## Language
- Always reply to the repo owner in Simplified Chinese: explanations, questions, progress updates,
  and final reports.
- Keep these in English: code, code comments, commit messages, branch names, file contents,
  and anything written into the repository.
- Keep technical terms, file names, function names and command output as they are, without translating them.
