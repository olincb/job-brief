# Context map

One line per file: what it owns, so an agent can go to the right place without
grepping. Update this map in the same change whenever a file moves or a module
is added. The design and its reasons are in `design.md`; this is only a map.

## Root

- `AGENTS.md` — the contract for working here: non-negotiables, workflow, this map's rule.
- `CLAUDE.md` — imports `AGENTS.md` for Claude Code.
- `README.md` — what the project is and the one install command.
- `pyproject.toml` — package metadata, the one runtime dependency (`google-auth`), pytest configuration.
- `LICENSE` — MIT.

## `jobbrief/` — the engine

- `__init__.py` — package docstring only.
- `__main__.py` — hands off to `cli.main`.
- `cli.py` — `python -m jobbrief`: argparse and the thin subcommands that read files and write under `--out`. The only module that touches the filesystem, and with `mail.py` one of the two that read the environment.
- `mail.py` — `send(to, subject, html, text)`: one email through SES over SMTP (multipart/alternative, STARTTLS on 587), plus the two failure-notice bodies. Reads the SES credentials and sender from the environment.
- `sources.py` — where postings come from: HTTP get, HTML stripping, `condense`, one generator per source, the enrichers, and `select_candidates` over the fetched pool.
- `llm.py` — `generate`, the one Gemini call with retries and the fallback model inside it, and the tolerant JSON parser.
- `rank.py` — `build_prompt` from template, profile, pipeline, and candidates; `finish` from the model's answer to the brief and sheet rows.
- `render.py` — Markdown to inline-styled HTML, the backlog line, the sheet-link footer, the heartbeat email body.
- `sheet.py` — the six per-user tab headers, `days_since_email` for the heartbeat rule, and the Sheets/Drive client: a service-account token, one authenticated request, the six tab and permission operations, and `init_sheet`/`share_sheet` on top.
- `registry.py` — the registry sheet: the `Users` and `Allowed` tab headers and `users_to_run`, which picks the active users a run serves and applies `ONLY_USERS`.
- `find_boards.py` — operator tool: turns company names into verified ATS slugs for `sources.base.json`.
- `data/prompt.md` — instructions to the model: scoring rubric, brief layout, JSON contract.
- `data/sources.base.json` — shared board slugs per ATS, Climatebase queries, and the whole-feed sources everyone runs against.

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
- `test_registry.py` — `users_to_run`: inactive rows dropped, no filter returns all active, `ONLY_USERS` restricts case-insensitively.
- `test_sheet.py` — `init_sheet` lays the six tabs, adds missing ones, writes every header but Profile, and shares.
- `test_fetch.py` — Greenhouse fetcher against the recording, unreachable board skipped, title filters, Seen dedup.
- `fixtures/greenhouse/gradle.json` — one Greenhouse board response, saved unmodified.
- `fixtures/posting.txt` — one posting as `condense` receives it.
- `fixtures/brief.md` — a hand-written sample brief with invented companies.
