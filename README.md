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

## Install

```
pip install git+https://github.com/olincb/job-brief@main
python -m jobbrief --help
```

Standard library only; Python 3.11 or newer.

## Web app

Sign-in, signup, and settings, in the `web` extra (Flask behind waitress). Run it
locally with placeholder values, or build the `Dockerfile`, which serves the same
command on port 8080:

```
pip install -e ".[web]"
python -m jobbrief.web
```

Set up a Google OAuth client first: a **web application** client in the same
Cloud project as the service account, with `<your base URL>/auth/callback` as an
authorized redirect URI. On the consent screen, list the identity scopes
(`openid`, `email`, `profile`) and `drive.file`, which signup asks for in a
second consent step, and publish the screen to Production. All four are
non-sensitive, so there is no verification review.

The app reads `GCP_OAUTH_CLIENT_ID`, `GCP_OAUTH_CLIENT_SECRET`, `SESSION_KEY` (any
long random string, which signs the session cookie), `SERVICE_ACCOUNT_JSON`,
`REGISTRY_SHEET_ID`, `OPERATOR_EMAIL`, `GEMINI_API_KEY` (signup drafts the
profile), and the SES variables `BRIEF_FROM`,
`SES_SMTP_HOST`, `SES_SMTP_USER`, `SES_SMTP_PASS`. Where those values come from
and where they are kept is the operator's runbook, in the ops repo.

## Removing a user

A user removes themselves from the settings page. The operator switches someone
off by deleting their row from the registry's `Allowed` tab: sign-in is refused
and the daily run skips them, and adding the row back turns them on again with
their settings and sheet untouched. Removing someone for good is the same two
edits the settings page makes, by hand and with no code: delete their row from
the `Users` tab, and remove the service account from the sharing list on their
sheet. The sheet stays with the user either way.

## Releasing

Production runs a tag, never `main`. To release: bump `version` in
`pyproject.toml`, commit, then `gh release create vX.Y.Z --generate-notes`.
Deploying it is the ops repo's job: dispatch `deploy.yml` with the tag for the
web app, and change the pin in the scheduled workflow for the daily job.

Status: design complete, implementation starting. Work is tracked in Issues.
