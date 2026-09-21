# job-brief

A periodic email of job postings ranked by a model against a plain-prose profile,
with a Google Sheet the user owns as memory and application tracker. This repo is
the engine: the fetchers, the ranking, the renderer, the web app, and the daily
run. One operator runs it for people they know; the schedule, the secrets and the
runbook live in a separate private repo.

Architecture, flows and every decision with its reason are in
[`docs/design.md`](docs/design.md). The intake form is
[`docs/questionnaire.md`](docs/questionnaire.md), one line per file is
[`docs/context-map.md`](docs/context-map.md), and contributors start at
[`AGENTS.md`](AGENTS.md).

## Setup

Python 3.11 or newer. `pip install git+https://github.com/olincb/job-brief@main`,
then `python -m jobbrief --help`. To work on the engine, `pip install -e ".[dev]"`
and run `python -m pytest`: the suite is offline and takes well under a second.
Fixtures and how to re-record them are in [`tests/README.md`](tests/README.md).

One-time setup, in order. Each step produces a value the operator keeps in the ops
repo and passes to the run as an environment variable.

1. Create a Google Cloud project and enable the Sheets API and the Drive API.
2. Create a service account in that project and download a JSON key. The whole
   contents of the key file go in `SERVICE_ACCOUNT_JSON`.
3. Create the registry spreadsheet in the operator's Drive by hand: a `Users` tab
   with the header row `email, sheet_id, active, frequency, added`, an `Allowed`
   tab with `email, added`, and the service account added as an editor. Its id
   goes in `REGISTRY_SHEET_ID`. (`python -m jobbrief init-sheet` lays out a
   *user's* six-tab sheet, not the registry. Signup creates those; rerunning it
   with `--sheet-id` adds tabs and headers a newer version introduced.)
4. Create a Gemini API key on the paid tier, in `GEMINI_API_KEY`.
5. Verify the sending domain in Amazon SES: publish its Easy DKIM records plus SPF
   and DMARC at the DNS provider, then request production access so the engine can
   send to addresses SES has not verified. Create SMTP credentials for the
   identity and set `SES_SMTP_HOST`, `SES_SMTP_USER`, `SES_SMTP_PASS`, and the
   sender address in `BRIEF_FROM`.
6. Put the operator's own address in `OPERATOR_EMAIL`, where failure notices and
   sign-in requests from uninvited addresses go. The operator signs up through the
   web app like anyone else, on their own `Allowed` row.

The web app is `python -m jobbrief.web`, installed with `pip install -e ".[web]"`.
Besides the variables above it reads `GCP_OAUTH_CLIENT_ID`,
`GCP_OAUTH_CLIENT_SECRET` and `SESSION_KEY`. Its OAuth client and its deployment
are the ops repo's; `docs/design.md` describes the flows it implements.

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
failed. Anything else is 0, one user's sheet failing included: that user gets a
`failed` row on their Runs tab, they and the operator are emailed, and the loop
carries on. Sheet ids, outcomes and counts go to stdout, skipped sources and
per-user errors to stderr; no address and no profile text is ever printed.

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
Every command takes `--out`; `--title-filter` and `--title-exclude` take one
pattern each and repeat.

## Adding a fetcher

`fetch_greenhouse` in `jobbrief/sources.py` is the model: a generator taking one
value, fetching through `get_json`, and yielding each job through
`posting(ats, company, job_id, title, location, url, posted_at, snippet)`, the
common shape every later stage reads. An unreachable board leaves `get` returning
`None` and its url in `SKIPPED`, so the generator yields nothing and the run
continues without it.

1. Write the generator in `sources.py` and register it in `FETCHERS` under a
   source name. That name is the first field of every posting id, and the label in
   the Runs row's picks-per-source count.
2. Add what it is called with to `jobbrief/data/sources.base.json`: board slugs for
   a company ATS, search queries for a query source, one placeholder for a whole
   feed, whose value is ignored. `find_boards.py` turns company names into
   verified ATS slugs.
3. If the list endpoint carries no job description, add an entry to `ENRICHERS`
   under the same source name. Enrichers fetch one page per candidate and run only
   after dedup and the title filters, so it is a handful of requests per run.
4. Record the board's response as a fixture and add a test that serves it through
   `sources.get`; see `tests/README.md` and `tests/test_fetch.py`.

## Changing the prompt

`jobbrief/data/prompt.md` is every instruction the model gets: the scoring rubric,
the brief's sections, and the JSON contract. `build_prompt` appends the pick
maximum and the profile, pipeline and candidate blocks in the order the template
names them, so the template has to go on naming them `## Candidate profile`,
`## Pipeline` and `## Candidates`.

The answer is one JSON object. `finish` turns each entry in `picks` — `id`, `fit`,
`reason`, `risk` — into a Postings row, drops any `id` that is not a candidate,
and counts picks per source for the Runs row; `brief_markdown` is the brief
itself, which `render` turns into the email. `reviewed` is asked for so the
Skipped section has a number to quote and is not read by the engine. Renaming a
field means changing `finish` in the same edit. `tests/test_rank.py` drives prompt
assembly and `finish` with a stand-in model answer, so a broken contract fails
there rather than in a live run.

## Releasing

Production runs a tag, never `main`. Bump `version` in `pyproject.toml`, commit,
then `gh release create vX.Y.Z --generate-notes`. Deploying it is the ops repo's
job.
