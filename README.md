# job-brief

A periodic email of job postings ranked by a model against a plain-prose profile,
with a Google Sheet the user owns as memory and application tracker. This repo is
the engine: fetching, ranking, rendering, the web app, and the daily run. The
schedule, the secrets and the runbook live in a separate private ops repo.

Decisions and their reasons are in [`docs/design.md`](docs/design.md), the intake
form in [`docs/questionnaire.md`](docs/questionnaire.md), one line per file in
[`docs/context-map.md`](docs/context-map.md), the contract in [`AGENTS.md`](AGENTS.md).

## Setup

Python 3.11 or newer. `pip install git+https://github.com/olincb/job-brief@v1.0.0`,
then `python -m jobbrief --help`. For engine work, `pip install -e ".[dev]"` and
`python -m pytest`; the suite is offline and takes under a second. Fixtures and
re-recording are in [`tests/README.md`](tests/README.md).

One-time setup, in order. Each step produces a value the run reads from the environment.

1. Create a Google Cloud project and enable the Sheets API and the Drive API.
2. Create a service account in that project and download a JSON key. The whole
   contents of the key file go in `SERVICE_ACCOUNT_JSON`.
3. Create the registry spreadsheet in the operator's Drive by hand: a `Users` tab
   with the header row `email, sheet_id, active, frequency, added`, an `Allowed`
   tab with `email, added`, and the service account as an editor. Its id goes in
   `REGISTRY_SHEET_ID`. (`python -m jobbrief init-sheet` lays out a *user's* sheet,
   not the registry; `--sheet-id` brings an existing one up to date.)
4. Create a Gemini API key on the paid tier, in `GEMINI_API_KEY`.
5. Verify the sending domain in Amazon SES: publish its Easy DKIM, SPF and DMARC
   records at the DNS provider, then request production access to send to addresses
   SES has not verified. Create SMTP credentials and set `SES_SMTP_HOST`,
   `SES_SMTP_USER`, `SES_SMTP_PASS`, and the sender address in `BRIEF_FROM`.
6. Put the operator's own address in `OPERATOR_EMAIL`, where failure notices and
   sign-in requests from uninvited addresses go. The operator signs up through the
   web app like anyone else, on their own `Allowed` row.

The web app is `python -m jobbrief.web`, installed with `pip install -e ".[web]"`.
Besides the variables above it reads `GCP_OAUTH_CLIENT_ID`, `GCP_OAUTH_CLIENT_SECRET`
and `SESSION_KEY`. Its OAuth client and its deployment belong to the ops repo.

## Running

`python -m jobbrief run` is the daily run and takes no arguments. It reads
`SERVICE_ACCOUNT_JSON`, `REGISTRY_SHEET_ID`, `GEMINI_API_KEY`, `OPERATOR_EMAIL`
and the four SES variables, fetches every source once, and serves each active
registry user whose frequency makes today a send day from that one pool.

```
ONLY_USERS=a@example.com,b@example.com python -m jobbrief run
```

`ONLY_USERS` restricts the run to those addresses end to end; nobody else is read,
emailed or written to. Empty or unset is everyone.

Exit code 1 means the fetch came back empty with sources skipped, or every user
failed; anything else is 0. A user whose sheet fails gets a `failed` row on their
Runs tab, an email to them and the operator, and the loop carries on. Sheet ids and
counts go to stdout, errors to stderr; no address or profile text is printed.

## Debugging one stage

The stage subcommands run one user's pipeline from files in a directory, `--out`
(default `./out`). Save the user's `Seen`, `Postings` and `Runs` tabs there as
`seen.json`, `postings.json` and `runs.json`, each a Sheets `values.get` response
(`{"values": [[header row], ...]}`), and their `Profile` text to a file.

| Command | Reads | Writes |
|---|---|---|
| `fetch --title-filter REGEX [--title-exclude REGEX] [--lookback-days N] [--sources FILE]` | the packaged board list, `seen.json` | `candidates.json`, `fetch_stats.json` |
| `prompt --profile FILE [--max-picks N] [--prompt FILE]` | `candidates.json`, `postings.json` | `prompt.txt` |
| `rank [--model M] [--fallback-model M] [--retries N]` | `prompt.txt`, `GEMINI_API_KEY` | `model.txt`, `rank_stats.json` |
| `finish [--model-output FILE]` | `model.txt`, `candidates.json` | `brief.md`, `postings_rows.json`, `seen_rows.json` |
| `render --sheet-id ID` | `brief.md`, `postings.json` | `brief.html` |
| `heartbeat [--days N]` | `runs.json`, `fetch_stats.json`, `postings.json` | prints `send` or `quiet`, and `heartbeat.html` when it sends |
| `log-run --outcome OUTCOME --emailed yes\|no` | `fetch_stats.json`, `postings_rows.json`, `rank_stats.json` | `run_row.json` |

Run them in that order to reproduce a user's brief without touching their sheet.
Every command takes `--out`; `--title-filter` and `--title-exclude` are repeatable.

## Adding a fetcher

`fetch_greenhouse` in `jobbrief/sources.py` is the model: a generator taking one
value, fetching through `get_json`, and yielding each job through
`posting(ats, company, job_id, title, location, url, posted_at, snippet)`, the
shape every later stage reads. An unreachable board leaves `get` returning `None`
and its url in `SKIPPED`, so the generator yields nothing and the run continues.

1. Write the generator in `sources.py` and register it in `FETCHERS` under a
   source name. That name is the first field of every posting id and the label in
   the Runs row's picks-per-source count, except that `fetch_wordpress` labels each
   board with its site's first DNS label.
2. Add what it is called with to `jobbrief/data/sources.base.json`: board slugs for
   a company ATS, search queries for a query source, one placeholder whose value
   is ignored for a whole feed. `find_boards.py` turns company names into
   verified slugs.
3. If the list endpoint carries no job description, add an entry to `ENRICHERS`
   under the same source name. Enrichers fetch one page per candidate and run only
   after dedup and the title filters, so it is a handful of requests per run.
4. Record the board's response as a fixture and add a test that serves it through
   `sources.get`; see `tests/README.md` and `tests/test_fetch.py`.

## Changing the prompt

`jobbrief/data/prompt.md` is every instruction the model gets: the scoring rubric,
the brief's sections, and the JSON contract. `build_prompt` appends the pick maximum
and the profile, pipeline and candidate blocks in the order the template names them,
so it must go on naming them `## Candidate profile`, `## Pipeline` and `## Candidates`.

The answer is one JSON object. `finish` turns each entry in `picks` — `id`, `fit`,
`reason`, `risk` — into a Postings row, drops any `id` that is not a candidate, and
counts picks per source for the Runs row; `brief_markdown` is the brief, which
`render` turns into the email. `reviewed` only gives the Skipped section a number
to quote. Renaming a field means changing `finish` too; `tests/test_rank.py`
exercises the contract with a stand-in answer, so a break fails there, not live.

## Releasing

Production runs a tag, never `main`. Bump `version` in `pyproject.toml`, commit,
then `gh release create vX.Y.Z --generate-notes`. Both the web app and the scheduled
job install a release tag; the ops repo says which.
