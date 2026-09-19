# Context map

One line per file: what it owns, so an agent can go to the right place without
grepping. Update this map in the same change whenever a file moves or a module
is added. The design and its reasons are in `design.md`; this is only a map.

## Root

- `AGENTS.md` — the contract for working here: non-negotiables, workflow, this map's rule.
- `CLAUDE.md` — imports `AGENTS.md` for Claude Code.
- `README.md` — what the project is, the install command, and the web app's OAuth and environment setup.
- `pyproject.toml` — package metadata, the one runtime dependency (`google-auth`), the `web` extra, pytest configuration.
- `Dockerfile` — the web app image: install the `web` extra, serve `python -m jobbrief.web`. `fly.toml` and the secrets are in the ops repo.
- `LICENSE` — MIT.

## `jobbrief/` — the engine

- `__init__.py` — package docstring only.
- `__main__.py` — hands off to `cli.main`.
- `cli.py` — `python -m jobbrief`: argparse and the thin subcommands that read files and write under `--out`. The only module that touches the filesystem, and with `mail.py` one of the two that read the environment.
- `mail.py` — `send(to, subject, html, text)`: one email through SES over SMTP (multipart/alternative, STARTTLS on 587), plus the two failure-notice bodies. Reads the SES credentials and sender from the environment.
- `sources.py` — where postings come from: HTTP get, HTML stripping, `condense`, one generator per source, the enrichers, and `select_candidates` over the fetched pool.
- `llm.py` — `generate`, the one Gemini call with retries and the fallback model inside it, and the tolerant JSON parser.
- `rank.py` — `build_prompt` from template, profile, pipeline, and candidates; `finish` from the model's answer to the brief and sheet rows.
- `profile.py` — signup's model stage: `draft_profile` from an `Answers` row and an optional resume to the prose profile and the `Settings` values, with the docx-to-text step and the title filters behind it.
- `render.py` — Markdown to inline-styled HTML, the backlog line, the sheet-link footer, the heartbeat email body.
- `sheet.py` — the six per-user tab headers, `days_since_email` for the heartbeat rule, and the Sheets/Drive client: a service-account token, one authenticated request, the tab, row and permission operations, and `init_sheet` on top.
- `registry.py` — the registry sheet: the `Users` and `Allowed` tab headers and `users_to_run`, which picks the active users a run serves from those on `Allowed`, and applies `ONLY_USERS`.
- `find_boards.py` — operator tool: turns company names into verified ATS slugs for `sources.base.json`.
- `data/prompt.md` — instructions to the model: scoring rubric, brief layout, JSON contract.
- `data/profile_prompt.md` — instructions to the model that drafts a profile: what to trust, what the Answers columns hold, and the profile's sections.
- `data/sources.base.json` — shared board slugs per ATS, Climatebase queries, and the whole-feed sources everyone runs against.

## `jobbrief/web/` — the web app

- `__init__.py` — package docstring only.
- `__main__.py` — `python -m jobbrief.web`: waitress serving the app on 8080.
- `app.py` — the Flask app: the `Allowed` lookup and the gate, the operator's invite notice, the routes, the signup stash between the form and the Drive consent, and every write to a user's sheet and registry row. The only web module that reads the environment.
- `form.py` — the signup form: the lists the questionnaire offers back to the user, one `Answers` row from a submitted form, and the tick marks a stored row puts back on it.
- `oauth.py` — Google's authorization-code flow: the consent URL, the code exchange, and the verified email from `userinfo`. One request function, faked in tests.
- `templates/` — `base.html` and the pages: `signin.html`, `invite.html`, `signup.html` (the questionnaire, also the retake), `settings.html`, `delete.html`, `removed.html`.

## `docs/`

- `design.md` — architecture, flows, and every decision with its reason. Source of truth.
- `questionnaire.md` — the intake form users fill in at signup; its sections map onto the profile.
- `context-map.md` — this file.

## `tests/` — offline pytest

Verify command: `python -m pytest`. Runs with no network; see `tests/README.md`.

- `README.md` — layout, the no-network guarantee, and how to re-record fixtures.
- `conftest.py` — blocks `urlopen` for every test; `fixture_dir` and `out` fixtures.
- `record_fixtures.py` — the deliberate network step: records boards raw and picks the condense posting.
- `test_condense.py` — years, remote, and pay lines survive condensing.
- `test_render.py` — numbered picks stay one list across blank lines; links, bold, sheet footer.
- `test_heartbeat.py` — the heartbeat rule over synthetic Runs rows.
- `test_registry.py` — `users_to_run`: inactive rows dropped, a missing `Allowed` row stops and restarts a user, no filter returns all active, `ONLY_USERS` restricts case-insensitively.
- `test_sheet.py` — `init_sheet` lays the six tabs, adds missing ones, and writes every header but Profile; the editor, row and cell operations.
- `test_web.py` — sign-in scopes, the gate (allowed, operator, unknown address), the invite email, state and verification refusals, signup end to end (submit, the Drive callback's sheet and registry writes, a lost stash, a model failure), and settings: what the page shows, what a save writes, pause, retake, delete-me.
- `test_profile.py` — the generator prompt from an invented `Answers` row, the title filters as typed, a Word resume to text, and the PDF attachment.
- `test_fetch.py` — Greenhouse fetcher against the recording, unreachable board skipped, title filters, Seen dedup.
- `fixtures/greenhouse/gradle.json` — one Greenhouse board response, saved unmodified.
- `fixtures/posting.txt` — one posting as `condense` receives it.
- `fixtures/brief.md` — a hand-written sample brief with invented companies.
