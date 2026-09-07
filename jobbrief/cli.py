"""Command line for one run, stage by stage. Every stage is a function over values in the
other modules; these subcommands read those values from files and write results under
--out (default ./out), so a run is reproducible from that directory alone:

  fetch      sources + Seen dump + title filters -> candidates.json
  prompt     template + profile + Postings dump + candidates -> prompt.txt
  rank       prompt.txt -> Gemini API -> model.txt
  finish     model output + candidates -> brief.md, postings_rows.json, seen_rows.json
  render     brief.md -> brief.html, with a link to the tracking sheet
  heartbeat  Runs dump -> "send" (and heartbeat.html) or "quiet"
  log-run    stats under --out -> run_row.json, one row for the Runs tab
  init-sheet create a sheet, or add missing tabs and headers to one, and share it

The model only ranks and writes, in one API call. Fetching, dedup against the sheet, and
building the rows to append all happen here so a run costs one request."""

import argparse
import json
import os
import sys
from datetime import datetime
from importlib.resources import files
from pathlib import Path

from jobbrief.llm import generate, parse_model_json
from jobbrief.rank import build_prompt, finish
from jobbrief.render import heartbeat_html, render
from jobbrief.sheet import create_sheet, days_since_email, ensure_layout, load_sheet_rows, share_sheet
from jobbrief.sources import SKIPPED, enrich, fetch_all, select_candidates


DATA = files("jobbrief.data")  # packaged defaults: the prompt template and the shared board list


def packaged_or(path, name):
    """A file the operator supplied, else the copy shipped inside the package."""
    return Path(path).read_text() if path else DATA.joinpath(name).read_text()


def cmd_fetch(args):
    sources = json.loads(packaged_or(args.sources, "sources.base.json"))
    seen = {row["url"] for row in load_sheet_rows(args.seen or args.out / "seen.json")}
    candidates = enrich(select_candidates(fetch_all(sources), seen, args.title_filter, args.title_exclude, args.lookback_days))
    (args.out / "candidates.json").write_text(json.dumps(candidates, indent=1))
    (args.out / "fetch_stats.json").write_text(json.dumps({"new_candidates": len(candidates), "skipped_sources": SKIPPED}))
    print(f"{len(candidates)} new candidates, {len(SKIPPED)} sources skipped", file=sys.stderr)


def cmd_prompt(args):
    text = build_prompt(
        packaged_or(args.prompt, "prompt.md"), Path(args.profile).read_text(),
        load_sheet_rows(args.postings or args.out / "postings.json"),
        json.loads((args.out / "candidates.json").read_text()), args.max_picks,
    )
    (args.out / "prompt.txt").write_text(text)


def cmd_rank(args):
    api_key = os.environ.get("GEMINI_API_KEY") or sys.exit("GEMINI_API_KEY is not set")
    models = [args.model] + ([args.fallback_model] if args.fallback_model and args.fallback_model != args.model else [])
    text, model, usage = generate((args.out / "prompt.txt").read_text(), models, api_key, args.retries, json_output=True)
    (args.out / "model.txt").write_text(text)
    (args.out / "rank_stats.json").write_text(json.dumps({"model": model, "tokens": usage.get("totalTokenCount", "")}))
    print(f"model {model}: {usage.get('promptTokenCount')} in, {usage.get('candidatesTokenCount')} out, "
          f"{usage.get('thoughtsTokenCount', 0)} thinking", file=sys.stderr)


def cmd_finish(args):
    result = parse_model_json(Path(args.model_output or args.out / "model.txt").read_text())
    candidates = json.loads((args.out / "candidates.json").read_text())
    brief_markdown, postings_rows, seen_rows = finish(result, candidates, datetime.now().strftime("%Y-%m-%d"))
    (args.out / "brief.md").write_text(brief_markdown)
    (args.out / "postings_rows.json").write_text(json.dumps({"values": postings_rows}))
    (args.out / "seen_rows.json").write_text(json.dumps({"values": seen_rows}))
    print(f"{len(postings_rows)} picks, {len(seen_rows)} marked seen", file=sys.stderr)


def cmd_render(args):
    postings = load_sheet_rows(args.postings or args.out / "postings.json")
    (args.out / "brief.html").write_text(render((args.out / "brief.md").read_text(), args.sheet_id, postings))


def read_json(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def cmd_heartbeat(args):
    """Decide whether a quiet run should still say hello. Sends when no email has gone
    out in `--days` days according to the Runs tab, so a single quiet day is silent but
    silence never lasts long enough to be mistaken for breakage."""
    quiet_days = days_since_email(load_sheet_rows(args.runs or args.out / "runs.json"), datetime.now().date())
    if quiet_days is not None and quiet_days < args.days:
        print("quiet")
        return
    stats = read_json(args.out / "fetch_stats.json", {})
    postings = load_sheet_rows(args.postings or args.out / "postings.json")
    (args.out / "heartbeat.html").write_text(heartbeat_html(quiet_days, stats, postings))
    print("send")


def cmd_log_run(args):
    stats = read_json(args.out / "fetch_stats.json", {})
    picks = len(read_json(args.out / "postings_rows.json", {"values": []})["values"])
    rank_stats = read_json(args.out / "rank_stats.json", {})
    row = [datetime.now().strftime("%Y-%m-%d"), stats.get("new_candidates", ""), picks,
           len(stats.get("skipped_sources", [])), args.outcome, args.emailed, rank_stats.get("model", ""), rank_stats.get("tokens", "")]
    (args.out / "run_row.json").write_text(json.dumps({"values": [row]}))


def cmd_init_sheet(args):
    """Idempotent. Creates the spreadsheet when no --sheet-id is given, and on every run
    brings the tabs and headers up to date and shares with --share-with if not already
    done. Safe to rerun after upgrading, e.g. when a new tab is introduced."""
    sheet_id = args.sheet_id
    if not sheet_id:
        sheet_id = create_sheet(args.title)
        print(f"created spreadsheet {sheet_id}")
    ensure_layout(sheet_id)
    if args.share_with:
        share_sheet(sheet_id, args.share_with)
    print(f"ready: https://docs.google.com/spreadsheets/d/{sheet_id}")


def main():
    parser = argparse.ArgumentParser(prog="jobbrief", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--out", type=Path, default=Path("out"), help="directory for this run's files (default: ./out)")

    p = sub.add_parser("fetch", parents=[common])
    p.add_argument("--sources", default="", help="board list JSON; default is the one shipped in the package")
    p.add_argument("--title-filter", action="append", metavar="REGEX", help="keep titles matching any; repeatable")
    p.add_argument("--title-exclude", action="append", metavar="REGEX", help="drop titles matching any; repeatable")
    p.add_argument("--seen", default="", help="gws dump of the Seen tab (default: <out>/seen.json)")
    p.add_argument("--lookback-days", type=int, default=3)
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("prompt", parents=[common])
    p.add_argument("--prompt", default="", help="instruction template; default is the one shipped in the package")
    p.add_argument("--profile", required=True, help="the user's prose profile, as a file")
    p.add_argument("--postings", default="", help="gws dump of the Postings tab (default: <out>/postings.json)")
    p.add_argument("--max-picks", type=int, default=10)
    p.set_defaults(func=cmd_prompt)

    p = sub.add_parser("rank", parents=[common])
    p.add_argument("--model", default="gemini-3.8-flash")
    p.add_argument("--fallback-model", default="gemini-3.5-flash")
    p.add_argument("--retries", type=int, default=8, help="total attempts, split evenly between the primary and fallback models")
    p.set_defaults(func=cmd_rank)

    p = sub.add_parser("finish", parents=[common])
    p.add_argument("--model-output", default="", help="(default: <out>/model.txt)")
    p.set_defaults(func=cmd_finish)

    p = sub.add_parser("render", parents=[common])
    p.add_argument("--sheet-id", default="", help="tracking sheet to link in the footer")
    p.add_argument("--postings", default="", help="gws dump of the Postings tab (default: <out>/postings.json)")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("heartbeat", parents=[common])
    p.add_argument("--runs", default="", help="gws dump of the Runs tab (default: <out>/runs.json)")
    p.add_argument("--postings", default="", help="gws dump of the Postings tab (default: <out>/postings.json)")
    p.add_argument("--days", type=int, default=4)
    p.set_defaults(func=cmd_heartbeat)

    p = sub.add_parser("log-run", parents=[common])
    p.add_argument("--outcome", required=True, choices=["sent", "quiet", "heartbeat", "failed"])
    p.add_argument("--emailed", required=True, choices=["yes", "no"])
    p.set_defaults(func=cmd_log_run)

    p = sub.add_parser("init-sheet")
    p.add_argument("--sheet-id", default="", help="existing sheet to bring up to date; omit to create one")
    p.add_argument("--title", default="Job Brief", help="title for a newly created sheet")
    p.add_argument("--share-with", default="", metavar="EMAIL", help="grant this Google account edit access")
    p.set_defaults(func=cmd_init_sheet)

    args = parser.parse_args()
    if hasattr(args, "out"):
        args.out.mkdir(parents=True, exist_ok=True)
    args.func(args)


if __name__ == "__main__":
    main()
