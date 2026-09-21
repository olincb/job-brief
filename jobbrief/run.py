"""The daily run: the date rules over a user's registry row and Runs tab, and the loop
that fetches every source once and then serves each user from that one pool.

Dates are stamped in UTC everywhere, so these are plain date arithmetic."""

import json
import sys
from importlib.resources import files

from jobbrief import mail
from jobbrief.llm import MODELS, RETRIES, generate, parse_model_json
from jobbrief.rank import build_prompt, finish
from jobbrief.registry import users_to_run
from jobbrief.render import heartbeat_html, render
from jobbrief.sheet import (RUNS_HEADER, append_rows, days_since_email, read_cell, read_tab,
                            service_account_token)
from jobbrief.sources import FETCHERS, SKIPPED, enrich, select_candidates


DATA = files("jobbrief.data")  # packaged defaults: the prompt template and the shared board list

# Days without an email before a quiet run says hello anyway: one quiet day stays silent,
# but silence never lasts long enough to be mistaken for breakage.
HEARTBEAT_DAYS = 4


def is_send_day(frequency, today):
    """Whether today is a send day on `frequency`: the registry's `daily`, `weekdays`
    or `weekly`."""
    if frequency == "weekdays":
        return today.weekday() < 5
    if frequency == "weekly":
        return today.weekday() == 0
    return True


def lookback(default_days, runs_rows, today):
    """How many days of postings this run covers: `default_days`, stretched to the gap
    since the last emailed brief so a missed or failed run loses no day. With no email on
    record, the default."""
    gap = days_since_email(runs_rows, today)
    return max(default_days, gap) if gap is not None else default_days


def runs_row(today, outcome, emailed, candidates=0, picks=0, model="", tokens="", sources="", note=""):
    """One row for the Runs tab, in RUNS_HEADER order. The skipped-source count is the
    whole run's, since every user is served from the same fetch."""
    return [str(today), candidates, picks, len(SKIPPED), outcome, emailed, model, tokens, sources, note]


def run_user(token, user, pool, api_key, today):
    """One user end to end, from their tabs to their email, returning their Runs row.
    Postings and Seen are appended only after the send succeeds, so the sheet and the inbox
    always agree."""
    sheet_id = user["sheet_id"]
    settings = {row["key"]: row["value"] for row in read_tab(token, sheet_id, "Settings")}
    title_filter = [line for line in settings["title_filter"].splitlines() if line.strip()]
    if not title_filter:
        return runs_row(today, "skipped", "no", note="empty filter")
    runs_rows = read_tab(token, sheet_id, "Runs")
    seen = {row["url"] for row in read_tab(token, sheet_id, "Seen")}
    pipeline = read_tab(token, sheet_id, "Postings")
    candidates, capped = select_candidates(
        pool, seen, title_filter, settings["title_exclude"].splitlines(),
        lookback(int(settings["lookback_days"]), runs_rows, today))
    candidates = enrich(candidates)
    prompt = build_prompt(DATA.joinpath("prompt.md").read_text(), read_cell(token, sheet_id, "Profile!A1"),
                          pipeline, candidates, int(settings["max_picks"]))
    text, model, usage = generate(prompt, MODELS, api_key, RETRIES, json_output=True)
    brief_markdown, postings_rows, seen_rows, sources = finish(parse_model_json(text), candidates, str(today))
    counts = dict(candidates=len(candidates), picks=len(postings_rows), model=model,
                  tokens=usage.get("totalTokenCount", ""), sources=sources, note="capped" if capped else "")
    if not postings_rows:
        quiet_days = days_since_email(runs_rows, today)
        if quiet_days is not None and quiet_days < HEARTBEAT_DAYS:
            return runs_row(today, "quiet", "no", **counts)
        stats = {"new_candidates": len(candidates), "skipped_sources": SKIPPED}
        mail.send(user["email"], "Job brief: still running", heartbeat_html(quiet_days, stats, pipeline),
                  "Still running. Nothing new to show today.")
        return runs_row(today, "heartbeat", "yes", **counts)
    mail.send(user["email"], f"Job brief: {len(postings_rows)} picks",
              render(brief_markdown, sheet_id, pipeline), brief_markdown)
    append_rows(token, sheet_id, "Postings", postings_rows)
    append_rows(token, sheet_id, "Seen", seen_rows)
    return runs_row(today, "sent", "yes", **counts)


def run(env, today):
    """The whole daily run: one fetch, then every user the registry serves, each user's
    failure isolated from the next. Returns the process exit code, which is non-zero only
    when the fetch itself failed or every user did, so a red run means the run."""
    # Every value up front, so a missing one costs nothing but the read; SES and the
    # sender address are mail.py's.
    api_key, key_json = env["GEMINI_API_KEY"], env["SERVICE_ACCOUNT_JSON"]
    registry_id, operator = env["REGISTRY_SHEET_ID"], env["OPERATOR_EMAIL"]
    only_users = [email for email in env.get("ONLY_USERS", "").split(",") if email.strip()]
    boards = json.loads(DATA.joinpath("sources.base.json").read_text())
    pool = [job for ats, fetcher in FETCHERS.items() for slug in boards.get(ats, []) for job in fetcher(slug)]
    if not pool and SKIPPED:
        print(f"no postings fetched, {len(SKIPPED)} sources skipped; no user was run", file=sys.stderr)
        return 1
    token = service_account_token(key_json)
    users = users_to_run(read_tab(token, registry_id, "Users"), read_tab(token, registry_id, "Allowed"), only_users)
    print(f"{len(pool)} postings, {len(SKIPPED)} sources skipped, {len(users)} users")
    failed = 0
    for user in users:
        sheet_id = user["sheet_id"]
        try:
            if is_send_day(user["frequency"], today):
                row = run_user(token, user, pool, api_key, today)
            else:
                row = runs_row(today, "skipped", "no")
            append_rows(token, sheet_id, "Runs", [row])
            logged = dict(zip(RUNS_HEADER, row))
            print(f"{sheet_id}: {logged['outcome']}, {logged['candidates']} candidates, {logged['picks']} picks")
        except (Exception, SystemExit) as exc:  # a dead end in llm.py is a SystemExit, and one user's is not the run's
            failed += 1
            print(f"{sheet_id}: {type(exc).__name__}", file=sys.stderr)
            try:
                append_rows(token, sheet_id, "Runs", [runs_row(today, "failed", "no", note=str(exc))])
            except (Exception, SystemExit) as write_failure:
                print(f"{sheet_id}: the failed row did not write either, {type(write_failure).__name__}", file=sys.stderr)
            notice = mail.operator_failure_notice(sheet_id, str(exc))
            mail.send(operator, "Job brief: a run failed", f"<pre>{notice}</pre>", notice)
            mail.send(user["email"], "Job brief: today's run did not finish",
                      f"<p>{mail.USER_FAILURE_NOTICE}</p>", mail.USER_FAILURE_NOTICE)
    return 1 if users and failed == len(users) else 0
