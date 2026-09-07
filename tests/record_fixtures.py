"""Re-record the network fixtures under tests/fixtures/.

    python -m tests.record_fixtures

The one place in the test tree that touches the network; the default test run never
imports it for its side effects. Each board is one fetch saved raw, so the fetcher tests
see exactly what the board returned. Sources are public job boards only.
"""

import json
import re
import sys
from pathlib import Path

from jobbrief.brief import condense, get, strip_html

FIXTURES = Path(__file__).parent / "fixtures"

# Boards saved unmodified as fixtures/<source>/<slug>.json. Small boards keep them readable.
BOARDS = [
    ("greenhouse", "gradle", "https://boards-api.greenhouse.io/v1/boards/gradle/jobs?content=true"),
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


def fetch_json(url):
    """Body text and parsed JSON, or exit. A proxy block page is HTML with a 200 or 403,
    and saving it would make a fixture that passes for the wrong reason."""
    body = get(url)
    if body is None:
        sys.exit(f"{url}: unreachable, nothing written")
    try:
        return body, json.loads(body)
    except json.JSONDecodeError:
        sys.exit(f"{url}: response is not JSON (a proxy block page?), nothing written")


def main():
    for source, slug, url in BOARDS:
        body, data = fetch_json(url)
        path = FIXTURES / source / f"{slug}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        print(f"{path.relative_to(FIXTURES.parent)}: {len(data['jobs'])} postings")

    _, data = fetch_json(POSTING_BOARD)
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
