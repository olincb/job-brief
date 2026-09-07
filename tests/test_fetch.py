import argparse
import json

from jobbrief import brief
from jobbrief.brief import SEEN_HEADER, fetch_greenhouse, posting, select_candidates

GRADLE_URL = "https://boards-api.greenhouse.io/v1/boards/gradle/jobs?content=true"


def serve(monkeypatch, responses):
    """Replace the engine's one network call with a lookup over recorded bodies. An
    unrecorded URL raises so a fetcher that changes its request fails visibly."""
    monkeypatch.setattr(brief, "get", lambda url, attempts=2: responses[url])


def test_greenhouse_fetcher_yields_recorded_postings(monkeypatch, fixture_dir):
    body = (fixture_dir / "greenhouse" / "gradle.json").read_text()
    serve(monkeypatch, {GRADLE_URL: body})
    jobs = list(fetch_greenhouse("gradle"))
    assert len(jobs) == len(json.loads(body)["jobs"])
    first = jobs[0]
    assert first["id"].startswith("greenhouse:gradle:")
    assert first["url"].startswith("https://") and first["title"] and first["posted_at"]
    assert "<" not in first["snippet"]


def test_unreachable_board_is_skipped_not_fatal(monkeypatch):
    serve(monkeypatch, {GRADLE_URL: None})  # what get returns after its retries fail
    assert list(fetch_greenhouse("gradle")) == []


GOOD_TITLES = ["Software Engineer, Platform", "Backend Engineer II"]
BAD_TITLES = ["Software Engineering Manager", "Senior Staff Software Engineer", "Frontend Software Engineer", "Account Executive"]
INCLUDE = ["software engineer", "backend"]
EXCLUDE = ["manager", "senior staff", "frontend"]


def pool():
    return [posting("fake", "board", n, title, "Remote", f"https://example.com/jobs/{n}", None, "short description")
            for n, title in enumerate(GOOD_TITLES + BAD_TITLES)]


def titles(candidates):
    return [c["title"] for c in candidates]


def test_title_filters_keep_good_titles_and_drop_bad():
    assert titles(select_candidates(pool(), set(), INCLUDE, EXCLUDE, 3)) == GOOD_TITLES


def test_empty_title_filter_keeps_every_title():
    assert titles(select_candidates(pool(), set(), [], [], 3)) == GOOD_TITLES + BAD_TITLES


def test_postings_already_seen_are_dropped():
    seen = {"https://example.com/jobs/0"}
    assert titles(select_candidates(pool(), seen, INCLUDE, EXCLUDE, 3)) == GOOD_TITLES[1:]


def test_fetch_command_writes_candidates_for_the_given_filters(out, monkeypatch):
    monkeypatch.setattr(brief, "FETCHERS", {"fake": lambda slug: iter(pool())})
    sources = out / "sources.json"
    sources.write_text(json.dumps({"fake": ["board"]}))
    seen = [["2026-09-01", "fake:board:0", "https://example.com/jobs/0"]]
    (out / "seen.json").write_text(json.dumps({"values": [SEEN_HEADER, *seen]}))
    brief.cmd_fetch(argparse.Namespace(out=out, sources=str(sources), seen="", title_filter=INCLUDE, title_exclude=EXCLUDE, lookback_days=3))
    assert titles(json.loads((out / "candidates.json").read_text())) == GOOD_TITLES[1:]
    assert json.loads((out / "fetch_stats.json").read_text())["new_candidates"] == 1
