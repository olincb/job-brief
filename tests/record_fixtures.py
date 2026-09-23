"""Re-record the network fixtures under tests/fixtures/.

    python -m tests.record_fixtures

The one place in the test tree that touches the network; the default test run never
imports it for its side effects. Each board is one fetch saved as returned, NEOGOV's map
key aside, so the fetcher tests see what the board returned. Sources are public job
boards only.
"""

import json
import re
import sys
from pathlib import Path
from xml.etree import ElementTree

from jobbrief.sources import condense, get, strip_html, wordpress_url

FIXTURES = Path(__file__).parent / "fixtures"

# Boards saved as fixtures/<source>/<slug>.json, or .xml for an RSS feed, requested with the
# headers their fetcher sends. Small boards keep them readable.
BOARDS = [
    ("greenhouse", "gradle", "https://boards-api.greenhouse.io/v1/boards/gradle/jobs?content=true", None),
    ("neogov", "whatcomcounty", "https://www.governmentjobs.com/careers/home/loadJobsOnMaps?agency=whatcomcounty",
     {"X-Requested-With": "XMLHttpRequest"}),
    ("wordpress", "joshswaterjobs-idaho", wordpress_url("joshswaterjobs.com/jwj_job?search=Idaho"), None),
    ("wordpress", "waconservationaction", wordpress_url("waconservationaction.org/job"), None),
    ("weworkremotely", "remote-programming-jobs", "https://weworkremotely.com/categories/remote-programming-jobs.rss", None),
]

# One posting for the condense test, saved as the text condense receives. Taken from a
# board whose engineering postings state years of experience, remote policy, and a pay
# range: the lines condense exists to keep. The first posting long enough to be cut and
# carrying all three is used, so re-recording survives individual postings closing.
POSTING_BOARD = "https://boards-api.greenhouse.io/v1/boards/planetscale/jobs?content=true"
REQUIRED_LINES = {
    "years": re.compile(r"\b\d+\+?\s*years?", re.IGNORECASE),
    "remote": re.compile(r"\bremote\b", re.IGNORECASE),
    "salary": re.compile(r"\$\s?\d"),
}


def fetch_board(url, headers=None):
    """Body text and parsed JSON, or an RSS feed's items, or exit. A proxy block page is HTML
    with a 200 or 403, and saving it would make a fixture that passes for the wrong reason."""
    body = get(url, headers=headers)
    if body is None:
        sys.exit(f"{url}: unreachable, nothing written")
    try:
        if not url.endswith(".rss"):
            return body, json.loads(body)
        items = ElementTree.fromstring(body).findall("channel/item")
    except (json.JSONDecodeError, ElementTree.ParseError):
        sys.exit(f"{url}: response is not JSON or XML (a proxy block page?), nothing written")
    if not items:
        sys.exit(f"{url}: feed has no items (a proxy block page?), nothing written")
    return body, items


def main():
    for source, slug, url, headers in BOARDS:
        body, data = fetch_board(url, headers)
        path = FIXTURES / source / (f"{slug}.xml" if url.endswith(".rss") else f"{slug}.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        # NEOGOV ships a third-party map key to every browser; it is not ours to publish.
        path.write_text(re.sub(r'"mapTilerKey":"[^"]*"', '"mapTilerKey":"REDACTED"', body))
        postings = data if isinstance(data, list) else data.get("jobs") or data.get("jobList") or []
        print(f"{path.relative_to(FIXTURES.parent)}: {len(postings)} postings")

    _, data = fetch_board(POSTING_BOARD)
    for job in data["jobs"]:
        text = strip_html(job.get("content"))
        if condense(text) != text and all(p.search(text) for p in REQUIRED_LINES.values()):
            (FIXTURES / "posting.txt").write_text(text)
            print(f"fixtures/posting.txt: {job['title']!r}, {len(text)} chars")
            break
    else:
        sys.exit("no posting on the board is long enough and states years, remote, and pay; pick another board")


if __name__ == "__main__":
    main()
