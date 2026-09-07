# job-brief

A periodic email of job postings worth applying to, ranked by an LLM against
a plain-prose profile of what you do and what you want, with a Google Sheet
you own as memory and application tracker. Built for an operator to run for
a group of people they know.

Each user signs in with Google, fills in a short form once, optionally
uploads a resume, and gets a sheet in their own Drive plus a daily,
weekday, or weekly email. Postings come from company applicant-tracking
boards, public job feeds, and sources added over time. The model only ranks
and writes; fetching, dedup, and bookkeeping are deterministic.

The architecture, flows, and every design decision with its reasons are in
[`docs/design.md`](docs/design.md). The intake form is
[`docs/questionnaire.md`](docs/questionnaire.md). Agents and contributors
start at [`AGENTS.md`](AGENTS.md). This repo is the engine; an operator's
deployment lives in a separate private repo with its own runbook.

Status: design complete, implementation starting. Work is tracked in Issues.
