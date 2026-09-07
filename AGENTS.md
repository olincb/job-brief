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

## Keep it small

This is one person's project, paid for out of their pocket, maintained in
bursts months apart, and used by people they know. Every piece of
infrastructure, abstraction, and configuration is something the operator
has to remember how it works after not looking at it since spring.
Complexity does not get amortized here; it just delays results.

- **The smallest change that closes the issue.** Not the most general, not
  the most robust, not the one that anticipates the next issue. If the next
  issue needs more, the next issue adds it.
- **Don't invent abstractions.** No helper, module, base class, or
  utility file exists to serve one call site; inline it. Extract a helper
  when it has real callers, counting any the design doc already names as
  coming. The goal is fewer made-up layers, not less repetition: a short
  helper shared by two call sites is fine, and inlining it into two long
  expressions makes things worse.
- **No configuration for a single value.** A constant in code with a
  comment beats a setting nobody will ever change. Settings exist for
  things that differ per user or per deployment, and the design doc lists
  them.
- **No new service, process, or dependency without stopping to ask.** A
  database, a queue, a cache, a framework, a CLI tool, a GitHub Action
  beyond the one scheduled job. The bar is: a file, a sheet, or the
  standard library cannot reasonably do it. Say what it costs per month.
- **No feature flags, compatibility shims, or migration scaffolding.**
  There is one deployment and one version. Change the code.
- **No hardening before there is a failure.** Retries, fallbacks, and
  validation exist where something has actually broken and the fix is
  recorded. Speculative try/except and defensive checks are removed on
  sight.
- **No optimization without a measurement.** The daily run can take ten
  minutes and nobody notices.
- **Recurring cost needs a named reason.** Anything that bills monthly, or
  scales with users, gets a sentence in the design doc saying why the free
  or flat option was not enough.
- **When in doubt, delete.** Fewer files, fewer options, fewer moving
  parts. If a change makes the context map longer, ask whether it should.

The test for any addition: would the operator, reading this cold in a
year, be glad it exists, or need to figure out what it is for? If the
answer is not clearly the first, leave it out and note the idea in the
issue instead.

## How to work

- Tasks live in GitHub Issues on this repo. Start from an issue; if none
  fits, open one before coding. Dependencies are "blocked by #n" in the
  body.
- Run `python -m pytest` before calling anything done. It runs offline in
  under a second. Recording fixtures is a separate step; see `tests/README.md`.
- Where work lands depends on how closely the operator is watching, not
  on its size. When the operator is approving each commit as it happens,
  commit straight to `main`; a pull request would be ceremony. When the
  operator has handed over a task to run with little oversight, work on a
  branch and open a pull request so there is one diff to read before it
  lands. If it is unclear which mode a session is in, ask at the start.
  Either way, close the issue from the commit or PR body with `Closes #n`,
  reference it with `#n` in earlier commits, and put the why in the body,
  not the what.
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
