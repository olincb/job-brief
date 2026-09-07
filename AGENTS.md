# Agent instructions

Read this first, then `docs/design.md`. Together they are the contract for
working in this repo.

## What this is

A service that emails each user a periodic brief of job postings ranked
against a prose profile they own, with a Google Sheet in their Drive as
memory and application tracker. Users sign in with Google and fill in a
form once; an operator runs it for people they know. This repo is the
engine: fetching, ranking, rendering, the web app, and the daily job. The
operator's deployment (secrets, accounts, schedule, runbook) lives in a
separate private repo and is never referenced here by value.

## Non-negotiables

- **No personal data in this repo.** No profiles, no emails, no sheet ids,
  no names, not in code, fixtures, docs, tests, or commit messages. Fixtures
  are recorded from public job boards only. Logs print sheet ids and
  counts, never addresses or profile text.
- **The design doc is the source of truth.** If a change contradicts
  `docs/design.md`, update the doc in the same change or stop and ask. Do
  not relitigate decisions recorded there; the reasons are written down.
- **Standard library only in the engine** unless a dependency replaces
  non-trivial domain logic and has been agreed. The web app may take a
  framework; say which and why.
- **Prose profiles, not schemas.** Preferences that involve judgment live
  in the profile text and are interpreted by the model. Structured fields
  exist only for mechanical things: dedup, title filters, lookback, pick
  cap, frequency. Add a field only when a failure recurs and a field would
  have prevented it.
- **One generator function per source.** A fetcher yields postings in the
  common shape and is skipped, not fatal, when its source is unreachable.
- **Users hold nothing.** No refresh tokens, no stored resumes. The user's
  OAuth token is used inside the signup request and discarded.
- **Tests run offline.** No network in the default test run. Re-recording
  fixtures is a documented, deliberate step.

## How to work

- Tasks live in GitHub Issues on this repo. Start from an issue; if none
  fits, open one before coding. Dependencies are "blocked by #n" in the
  body.
- Run `python -m pytest` before calling anything done. It runs offline in
  under a second. Recording fixtures is a separate step; see `tests/README.md`.
- Commit to `main`. No branches or pull requests unless asked; with one
  contributor they are ceremony. Close the issue from the commit body with
  `Closes #n`, and reference it with `#n` in earlier commits of a
  multi-commit task. What would have been a PR description goes in the
  commit body: why, not what.
- Small commits, conventional messages (`feat:`, `fix:`, `docs:`,
  `refactor:`, `test:`). No amending. Do not commit or push without the
  operator's explicit okay.
- Study existing patterns before adding new ones. Propose structural
  changes before making them.
- Comment why, not what. Keep functions small. Prefer a deep module with a
  narrow interface over helpers scattered across files.

## Map

- `docs/design.md` — architecture, flows, decisions and their reasons.
- `docs/questionnaire.md` — the intake form users fill in at signup.
- `docs/context-map.md` — one line per file and what it owns. Update it in
  the same change whenever a file moves or a module is added.
- `jobbrief/` — the package. See the context map.
- `tests/`, `tests/fixtures/` — pytest, recorded responses. `tests/README.md`
  covers the layout and how to re-record.
