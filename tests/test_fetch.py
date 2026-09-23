import argparse
import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from jobbrief import cli, sources
from jobbrief.sheet import SEEN_HEADER
from jobbrief.sources import CANDIDATE_CAP, enrich_neogov, fetch_greenhouse, fetch_neogov, is_recent, posting, select_candidates

GRADLE_URL = "https://boards-api.greenhouse.io/v1/boards/gradle/jobs?content=true"
WHATCOM_URL = "https://www.governmentjobs.com/careers/home/loadJobsOnMaps?agency=whatcomcounty"


def serve(monkeypatch, responses):
    """Replace the engine's one network call with a lookup over recorded bodies. An
    unrecorded URL raises so a fetcher that changes its request fails visibly."""
    monkeypatch.setattr(sources, "get", lambda url, attempts=2, headers=None: responses[url])


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


def test_neogov_fetcher_yields_recorded_postings(monkeypatch, fixture_dir):
    body = (fixture_dir / "neogov" / "whatcomcounty.json").read_text()
    serve(monkeypatch, {WHATCOM_URL: body})
    jobs = list(fetch_neogov("whatcomcounty"))
    assert len(jobs) == len(json.loads(body)["jobList"])
    first = jobs[0]
    job_id = first["id"].removeprefix("neogov:whatcomcounty:")
    assert job_id and first["url"].startswith(f"https://www.governmentjobs.com/careers/whatcomcounty/jobs/{job_id}/")
    assert first["title"] and first["location"] and first["snippet"]
    assert is_recent(first["posted_at"], 365 * 100)


def test_unreachable_agency_is_skipped_without_a_retry(monkeypatch):
    calls = []

    def not_found(req, timeout):
        calls.append((req.full_url, req.get_header("X-requested-with")))
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", None, None)

    monkeypatch.setattr(urllib.request, "urlopen", not_found)
    monkeypatch.setattr(sources, "SKIPPED", [])
    assert list(fetch_neogov("whatcomcounty")) == []
    assert calls == [(WHATCOM_URL, "XMLHttpRequest")] and sources.SKIPPED == [WHATCOM_URL]


def test_neogov_enricher_swaps_the_excerpt_for_the_full_description(monkeypatch):
    job = posting("neogov", "agency", 1, "Technician", "Bellingham, WA", "https://example.com/jobs/1", None,
                  "Full-Time | $20.00 - $30.00 Hourly | Opening paragraph...")
    page = '<script type="application/ld+json">{"@type": "JobPosting", "description": "&lt;p&gt;Opening paragraph.&lt;/p&gt;&lt;p&gt;Two years of field experience.&lt;/p&gt;"}</script>'
    serve(monkeypatch, {job["url"]: page})
    assert enrich_neogov(job) == "Full-Time | $20.00 - $30.00 Hourly | Opening paragraph.\nTwo years of field experience."


def test_neogov_enricher_without_json_ld_returns_none(monkeypatch):
    job = posting("neogov", "agency", 1, "Technician", "", "https://example.com/jobs/1", None, "snippet")
    serve(monkeypatch, {job["url"]: "<html>no structured data</html>"})
    assert enrich_neogov(job) is None


GOOD_TITLES = ["Software Engineer, Platform", "Backend Engineer II"]
BAD_TITLES = ["Software Engineering Manager", "Senior Staff Software Engineer", "Frontend Software Engineer", "Account Executive"]
INCLUDE = ["software engineer", "backend"]
EXCLUDE = ["manager", "senior staff", "frontend"]


def pool():
    return [posting("fake", "board", n, title, "Remote", f"https://example.com/jobs/{n}", None, "short description")
            for n, title in enumerate(GOOD_TITLES + BAD_TITLES)]


def titles(candidates):
    return [c["title"] for c in candidates]


def dated_pool(count):
    """`count` matching postings a minute apart, newest first."""
    now = datetime.now(timezone.utc)
    return [posting("fake", "board", n, "Backend Engineer", "Remote", f"https://example.com/jobs/{n}",
                    (now - timedelta(minutes=n)).isoformat(), "short description")
            for n in range(count)]


def test_title_filters_keep_good_titles_and_drop_bad():
    assert titles(select_candidates(pool(), set(), INCLUDE, EXCLUDE, 3)[0]) == GOOD_TITLES


def test_postings_already_seen_are_dropped():
    seen = {"https://example.com/jobs/0"}
    assert titles(select_candidates(pool(), seen, INCLUDE, EXCLUDE, 3)[0]) == GOOD_TITLES[1:]


def test_over_the_cap_keeps_the_newest():
    candidates, _ = select_candidates(dated_pool(200), set(), INCLUDE, EXCLUDE, 3)
    assert [c["id"] for c in candidates] == [f"fake:board:{n}" for n in range(CANDIDATE_CAP)]


def test_capped_is_reported_only_when_the_cap_bites():
    assert select_candidates(dated_pool(200), set(), INCLUDE, EXCLUDE, 3)[1] is True
    assert select_candidates(dated_pool(10), set(), INCLUDE, EXCLUDE, 3)[1] is False


def test_fetch_command_writes_candidates_for_the_given_filters(out, monkeypatch):
    monkeypatch.setattr(cli, "FETCHERS", {"fake": lambda slug: iter(pool())})
    sources_file = out / "sources.json"
    sources_file.write_text(json.dumps({"fake": ["board"]}))
    seen = [["2026-09-01", "fake:board:0", "https://example.com/jobs/0"]]
    (out / "seen.json").write_text(json.dumps({"values": [SEEN_HEADER, *seen]}))
    cli.cmd_fetch(argparse.Namespace(out=out, sources=str(sources_file), seen="", title_filter=INCLUDE, title_exclude=EXCLUDE, lookback_days=3))
    assert titles(json.loads((out / "candidates.json").read_text())) == GOOD_TITLES[1:]
    stats = json.loads((out / "fetch_stats.json").read_text())
    assert (stats["new_candidates"], stats["capped"]) == (1, False)
