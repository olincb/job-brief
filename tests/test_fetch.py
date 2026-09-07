import argparse
import json

from jobbrief import brief
from jobbrief.brief import SEEN_HEADER, fetch_greenhouse, posting

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


def fake_fetcher(slug):
    for n, title in enumerate(GOOD_TITLES + BAD_TITLES):
        yield posting("fake", slug, n, title, "Remote", f"https://example.com/jobs/{n}", None, "short description")


def run_fetch(out, monkeypatch):
    monkeypatch.setattr(brief, "FETCHERS", {"fake": fake_fetcher})
    sources = out / "sources.json"
    sources.write_text(json.dumps({
        "fake": ["board"],
        "title_filter": ["software engineer", "backend"],
        "title_exclude": ["manager", "senior staff", "frontend"],
    }))
    brief.cmd_fetch(argparse.Namespace(sources=str(sources), person_sources="", seen=str(out / "seen.json"), lookback_days=3))
    return [c["title"] for c in json.loads((out / "candidates.json").read_text())]


def test_title_filters_keep_good_titles_and_drop_bad(out, monkeypatch):
    assert run_fetch(out, monkeypatch) == GOOD_TITLES


def test_postings_already_in_seen_are_dropped(out, monkeypatch):
    seen = [["2026-09-01", "fake:board:0", "https://example.com/jobs/0"]]
    (out / "seen.json").write_text(json.dumps({"values": [SEEN_HEADER, *seen]}))
    assert run_fetch(out, monkeypatch) == GOOD_TITLES[1:]
