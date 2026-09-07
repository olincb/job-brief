# Job brief: design

Design for a multi-user job brief service. Decisions are recorded with
their reasons so they are not relitigated during implementation. Where a
choice was informed by running an earlier prototype for a small number of
users, the section says so.

## Goal

Each user gets a periodic email of job postings ranked against a written
profile, with a Google Sheet they own as memory and application tracker.
Onboarding is a sign-in and a form, not a conversation with the operator.
The operator's cost in money and attention stays near zero. Users are
people the operator knows; this is not a product for strangers, and several
choices below assume that.

## Shape

| Piece | Runs on | Acts as | Holds secrets | Holds state |
|---|---|---|---|---|
| Engine | public GitHub repo | nothing | none | none |
| Daily job | GitHub Actions, scheduled, in a private ops repo that pins the engine | service account; Gemini key; SES SMTP credentials | those three | none |
| Web app: sign-in, signup form, settings | Fly.io, one auto-stop Machine, Flask behind waitress, server-rendered | Google OAuth client for identity; service account for Sheets and Drive | OAuth client secret, session key, service account key | transient signup stash between form submit and Drive consent; lost on restart |
| Registry sheet | Drive, owned by the service account | | | `Users`: email, sheet_id, active, frequency, added. `Allowed`: email, added. |
| Per-user sheet | the user's own Drive, service account as editor | | | Answers, Profile, Settings, Postings, Seen, Runs |
| Email | Amazon SES, `brief@<your-domain>`, production access | | SMTP credentials | |

Nothing per user is stored anywhere except a registry row and their own
sheet. No database. No refresh tokens. Everything is in one Google Cloud
project plus one AWS account, both the operator's.

The registry sheet is not created by the engine: there is no hand-seeding
path for rows, so the operator makes the sheet once by hand with the two
tabs and their headers, and the operator's own account signs up like anyone.
The run reads it through `REGISTRY_SHEET_ID`, which with `ONLY_USERS` (see
Scoping) are the only two settings the run takes besides credentials.

The engine is standard library with one exception: `google-auth` signs the
service account's JWT, because the standard library has no RSA and that is
the kind of domain logic a dependency is for. The web app adds Flask and
waitress in a `web` extra, so installing the engine alone pulls neither.
Flask because the app is forms, redirects, and a signed cookie, which is
what it ships; waitress because it is one process with threads, and signup
keeps state in that process between two requests.

## Flows

### Sign in and gate

Google OAuth, authorization-code flow with a confidential client, httpOnly
session cookie, server-rendered pages. Sign-in asks for identity scopes
only: `openid`, `email`, `profile`. `drive.file` is requested in a second
consent step at the moment signup is about to create the sheet, so nobody
sees the form before the gate and no Drive token ever sits in a cookie.
All four scopes are classified non-sensitive, so the app is published to
Production with no verification review and no user cap.

The returned verified email is matched, case-insensitively, against the
`Allowed` tab. The operator's address, a deployment value rather than
anything in the engine, is always allowed. An
unknown address sees an invite-only page and triggers one email to the
operator naming the address. Nothing else is created, so an uninvited
login costs nothing. A signed approve link in that email is a later
addition; until then approval is adding a row to `Allowed`.

Why not a SPA with PKCE: a backend exists regardless for Gemini, SES, the
registry and the service account, so PKCE would only move sheet creation
into the browser at the cost of tokens in JavaScript and a second token
path. The server-side flow is also the pattern that gates a personal
website later. Google's recommended "code model" for web apps is this flow
with a popup in front, so the door stays open.

### Signup

An allowed but unregistered user sees the questionnaire as a form: ranked
lists, fine/meh/no grids, a few numbers, tool proficiency as job / class /
heard-of, certifications, an optional resume upload (PDF native, Word
converted to text), and two free-text boxes, "about you" and "about what
you want," framed as the place for career-specific detail the generic
questions miss.

Signup is three requests. The form is behind the gate, and nothing touches
Drive until the last step:

1. On submit, call Gemini on the paid tier with the answers and the resume
   to draft a prose `Profile`. Resume is ground truth for experience and
   skills, answers for preferences and hard lines, free text is high-signal
   detail to quote rather than summarize, and contact details are never
   copied into the profile. Derive the title filters and the condense
   vocabulary from the answers. Keep answers, profile, and settings in the
   web process, keyed by the session; discard the resume bytes. A model
   failure stops here, before anything exists.
2. Redirect to Google for `drive.file`, with the signed-in email as the
   login hint so consent is one click on the same account.
3. In the callback, with a token seconds old: create a spreadsheet in the
   user's Drive with the tabs above, add the service account as editor,
   write the raw answers to `Answers` (free text in its own columns so the
   operator can read them across users), the profile to `Profile`, and the
   mechanical settings to `Settings`. Append a registry row with `active`
   set to `no`, send a welcome email with the sheet link, and discard the
   token and the stashed answers. The `Profile` tab is what persists and is
   editable.

A new user is inactive until the operator has read the generated profile
and set `active` to `yes`; this is the draft state, and it is the same cell
the user's own pause toggles. The stash between steps 1 and 3 lives in the
one web process and is lost if the Machine restarts, in which case the user
submits the form again.

Verified against Google's reference: `spreadsheets.create` and
`permissions.create` both accept `drive.file`, and the Sheets API accepts
it for reading and writing files the app created. Scope sensitivity
measures how much existing data a scope reaches, not how powerful its
operations are; `drive.file` can share and delete, but only the file the
app just made.

### Daily run

1. Fetch every source once into a raw pool: company boards, Climatebase,
   Hacker News, RemoteOK, Himalayas, Apple, and whatever is added later.
2. For each user whose frequency makes today a send day: read their tabs,
   drop postings already in `Seen` or older than the lookback (stretched
   to cover the gap since their last send), apply their title filters,
   condense each posting to its requirements, one Gemini call with the
   primary-then-fallback retry budget, append `Seen` and `Postings`,
   render HTML, send via SES, append a `Runs` row with candidates, picks,
   model, tokens, and picks per source.
3. Quiet day: heartbeat rule, counted from the last send day.
4. Non-send day: a `skipped` Runs row and nothing else. Picks are never
   appended to a sheet without an email, so the sheet and the inbox always
   agree.
5. A failure for one user logs a `failed` row, emails the user and the
   operator, and the loop continues.

Logs carry sheet ids and counts, never addresses or profile text.

### Settings and exit

Profile text in an editable box, title filters, frequency (daily,
weekdays, weekly), pick cap, pause, and "retake the questionnaire," which
regenerates the profile from fresh answers. Recent `Runs` rows are shown.
Frequency and pause write to the registry row, not the user's sheet, so the
daily run decides a non-send day without opening the sheet; the rest write
to `Settings`. Weekly users get their brief on Monday.
Delete-me removes the registry row and the service account's editor
access; the sheet stays with the user. The operator's removal path is the
same two edits.

## Sources

Everyone runs against everything. There are no source packs. Per-user title
filters are the selector, and they are what keeps the Gemini bill per user
proportional to that user's market rather than the pool. Fetch cost is per
run, not per user.

Guardrails:

- A user with an empty title filter is not run. Filters are generated from
  the questionnaire's role ranking, with a small operator-maintained
  mapping from role types to title words such as technician, specialist,
  coordinator, assistant, analyst, intern, and are editable in settings.
- Candidates per user per run are capped around 150; hitting the cap is
  flagged as a filter that is too loose.

Coverage is measured, not configured. Per user per run: candidate count,
pick count, and which source each pick came from, all of which the run
already knows. The model is not asked to score candidates it does not pick;
a fit distribution was considered and dropped because it changed the JSON
contract to feed one heuristic that counts serve as well. A user with under
five candidates, or zero picks with candidates present, for five runs is
flagged "sources thin" in the operator digest. The questionnaire asks
"job boards or employers you already check," which is a request queue for
the next fetcher, not a selector.

Condensing a posting to its requirement lines keys on vocabulary, and the
stack words that catch a software posting miss a license or certification
in another field. The vocabulary is per user, taken from the questionnaire's
tools and certifications answers and stored in `Settings`, so a
conservation-district posting's "pesticide applicator certification" reaches
the model for the user it matters to.

Known gap at design time: public-sector sources. Washington state, county
and city governments, WWU and conservation districts post through NEOGOV
(governmentjobs.com, careers.wa.gov). A NEOGOV fetcher is the first new
source to build.

## Preferences: prose over schema

The profile is prose and the model interprets it, including hard filters.
This held up in the prototype: the one real ranking miss in early use was a
gap in what the prose said, fixed by adding two paragraphs, not by adding a
field. Structured fields stay only where Python does the job better
and no judgment is involved: dedup, title filters, lookback, pick cap,
frequency. A structured field is added only when a failure recurs and a
field would have prevented it.

## Email

SES over any Gmail route. The credential is send-only and project-owned,
not tied to anyone's personal account, and rotating it touches nothing
else. Domain verified with Easy DKIM records published at your DNS provider,
plus SPF and DMARC records. Production access requested before onboarding anyone
beyond the first two users; until then the operator verifies each
recipient by hand in the AWS console, the recipient clicks the Amazon link,
and the signup page says so. The code speaks SMTP only and never calls the
SES API, so there is no AWS request signing anywhere. Hard bounces are
handled by SES's account-level suppression list, which is sufficient
because every recipient is known personally.

## Operations

- **Schedule.** GitHub Actions cron at 22:00 UTC. Every UTC time from
  08:00 to 23:59 shares its calendar date with all continental US zones,
  so dates are stamped in UTC everywhere and there is no timezone
  configuration.
- **Scoping.** `ONLY_USERS`, a list of emails, restricts the run to those
  users end to end and treats everyone else as a non-send day. It is a
  manual-trigger input on Actions and an env var locally. This replaces a
  dry-run mode.
- **Operator digest.** Immediate email on any user failure or a
  "sources thin" flag. Otherwise a Monday summary: users, sends, quiet
  days, median fit per user, source attribution.
- **Gemini** on the paid tier, with a budget alert on the project, so no
  user's profile is training data.
- **Two repos.** The engine is public and contains no personal data, not
  even in logs. The ops repo is private and holds the workflow, secrets,
  and nothing else.

## Documentation split

Documentation follows the same line as the code. Anything true for anyone
running their own copy lives in the engine repo. Anything that names an
account, a region, a URL, a secret's location, or a person lives in the
ops repo. The ops repo is the runbook, written for an operator who has not
thought about the system in months and has just received a failure email.

Engine repo, public:

- This design doc, minus anything deployment-specific.
- How each component behaves: the daily run, the flows, the sources model,
  the sheet layout, Runs tab semantics, the heartbeat rule.
- How to add a fetcher, change the prompt, run locally against one user.
- Generic setup: create a service account with the Sheets and Drive APIs
  enabled, create a web-application OAuth client, verify a domain in SES.
  Steps, never the operator's values.

Ops repo, private, in this order because the reader arrives from a failure
email:

1. One paragraph saying what this is and that the engine lives elsewhere.
2. Failure diagnosis: no email for anyone, no email for one person, a
   failure email arrived, Google sign-in stopped working. Each with where
   to look first.
3. Procedures, as numbered steps that end with how to verify success: add
   a user, remove a user, run for one user now, pause everything, change
   the schedule, bump the engine version, add a source, respond to a
   failure email.
4. Inventory, one table: Google Cloud project and console link, service
   account email and key location, OAuth client and consent-screen status,
   Fly app name and region, SES identity, region and production-access
   status, DNS records and what each is for, both GitHub repos, the
   registry sheet URL.
5. Secrets map: each secret, which system holds it, which components read
   it, exact rotation steps. The service account key is the only secret in
   two places.
6. Costs: what runs each bill and where to see it. Gemini in the Cloud
   console, SES in AWS, Fly's dashboard, Actions minutes.
7. Deployment-specific decisions, such as the 22:00 UTC schedule and the
   AWS region, with a link back to this document for the rest.

## Working with agents

The engine repo carries a short `AGENTS.md` (imported by `CLAUDE.md`): what
the project is, the non-negotiables (no personal data in the repo or in
logs, standard library unless justified, prose profiles over schemas, one
generator function per source), and pointers to this document and the
runbook. If a change contradicts this document, the document is updated in
the same commit or the change stops. A context map lists each module and
what it owns.

Tests are pytest with recorded fixtures and no network in the default run:
fetchers against saved board responses, condense against saved postings,
the renderer against sample Markdown, the heartbeat against synthetic Runs
rows. Re-recording fixtures is a documented procedure. A single verify
command runs before any change is called done.

Work is tracked in GitHub Issues on the engine repo, one milestone per
phase, dependencies noted as "blocked by" in the body. In-repo trackers
built for agents, such as beads, were considered and passed over: Issues
is standard, fully operable by agents through `gh`, and a young tool like
beads may well be superseded within a year. This document is the
architecture and the runbook is the operations; there is no other process
document.

The ops repo's `AGENTS.md` is two lines: runbook first, never print or
commit secrets, test workflow changes with `ONLY_USERS` set to the
operator.

`AGENTS.md` also carries a "Keep it small" section: the project is one
person's, paid for out of pocket, maintained in bursts, and every piece of
infrastructure or abstraction is something the operator must relearn
later. Its rules are the smallest change that closes the issue, no
invented abstractions, no configuration for a single value, no new service
or dependency without asking and naming the monthly cost, no flags or
shims, no hardening before a failure, no optimization without a
measurement, and delete when in doubt. It was added during the first
implementation issues when the pull toward generality first showed.

## Security and privacy stance

- Users own their sheets and can revoke the bot at any time by removing
  the editor. The operator can read a sheet while the bot has access, and
  users are told so.
- Nothing per user is stored server-side beyond email and sheet id.
  Resumes are not stored. Tokens are not stored.
- Profile text is sent to Google's paid API, which does not train on it.
- Adding any sensitive or restricted scope later reopens Google's
  verification process. Staying on `drive.file` keeps the app permanently
  outside it.
- Rotating the service account key touches the web app and the ops repo.
  Rotating the OAuth client secret touches only the web app. Users hold
  nothing and are never affected by either.

## Build order

The web app comes before the daily job. Every user enters through signup,
so the run loop reads tabs that signup wrote and every end-to-end test
starts at the real entry point; a hand-seeded layout would only drift from
the generated one. The earlier prototype already delivers briefs, so
nothing is gained by racing the job out first.

Four phases, one milestone each. Foundations: the Sheets and Drive client
on the service account, the six-tab sheet layout, the registry and
scoping, SES send. Web app: sign-in and gate, signup with sheet creation,
profile generation from answers and resume, settings. Daily job: send-day
rule and stretched lookback, the candidate cap and empty-filter guard, the
run loop that fetches once and then serves every user, engine docs. Sources
and digest: the operator digest, NEOGOV, per-user condense vocabulary.
Already built from the prototype: the fetchers, condensing, the ranking
prompt and JSON contract, the fallback-model retry budget, the renderer,
and the heartbeat rule. Issues carry the detail.

## Verified and open

Verified: `drive.file` covers create, populate and share; it is
non-sensitive on both the Drive and Sheets scope pages; Gemini accepts
PDFs inline; SES sandbox behavior and production-access path; Fly
scheduled Machines and Actions cron characteristics.

Open, to test with real credentials before code depends on them:

1. A service account can write to a sheet in a personal Drive that was
   shared to it as editor. Expected yes; load-bearing.
2. SES production access turnaround, requested alongside publishing the
   OAuth app so the first friend's path has no manual step.

## Deliberately unspecified

The signed approve link, brand verification of the consent screen,
per-posting normalization before ranking, and any notion of users beyond
people the operator knows. Each has a clear trigger for when to build it,
and none changes the shape above.
