# Context map

One line per file: what it owns, so an agent can go to the right place without
grepping. Update this map in the same change whenever a file moves or a module
is added. The design and its reasons are in `design.md`; this is only a map.

## Root

- `AGENTS.md` — the contract for working here: non-negotiables, workflow, this map's rule.
- `CLAUDE.md` — imports `AGENTS.md` for Claude Code.
- `README.md` — what the project is and the one install command.
- `pyproject.toml` — package metadata, no runtime dependencies, pytest configuration.
- `LICENSE` — MIT.

## `jobbrief/` — the engine

- `__init__.py` — package docstring only.
- `__main__.py` — `python -m jobbrief`; hands off to `brief.main`.
- `brief.py` — the whole engine in one module. Each stage is a function over values: fetchers (one generator per source), `condense`, `select_candidates`, `build_prompt`, `generate` (the one Gemini call, with retries and fallback), `finish`, `render`, the heartbeat rule, and `init_sheet` over gws. The subcommands at the bottom read files and write under `--out`.
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
- `test_fetch.py` — Greenhouse fetcher against the recording, unreachable board skipped, title filters, Seen dedup.
- `fixtures/greenhouse/gradle.json` — one Greenhouse board response, saved unmodified.
- `fixtures/posting.txt` — one posting as `condense` receives it.
- `fixtures/brief.md` — a hand-written sample brief with invented companies.
