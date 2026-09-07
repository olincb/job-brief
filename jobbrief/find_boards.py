#!/usr/bin/env python3
"""Turn company names into verified sources.json entries.

    scripts/find_boards.py "Grafana Labs" Tailscale "Form Energy"

For each name, tries plausible slugs on Greenhouse, Ashby, and Lever and prints
the ones that return postings, with a description snippet so a slug collision
(the same word used by an unrelated company) is obvious before you add it.
"""

import json
import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

URLS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{}/jobs?content=true",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{}",
    "lever": "https://api.lever.co/v0/postings/{}?mode=json",
}


def variants(name):
    base = re.sub(r"[^a-z0-9 ]", "", name.lower())
    words = base.split()
    joined = "".join(words)
    out = [joined, "-".join(words), words[0]]
    out += [joined + suffix for suffix in ("inc", "hq", "io", "labs", "energy", "tech")]
    return list(dict.fromkeys(v for v in out if v))


def probe(ats, slug):
    req = urllib.request.Request(URLS[ats].format(slug), headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.load(resp)
    except (urllib.error.URLError, json.JSONDecodeError):
        return None
    jobs = data.get("jobs", data) if isinstance(data, dict) else data
    if not isinstance(jobs, list) or not jobs:
        return None
    first = jobs[0]
    desc = first.get("content") or first.get("descriptionHtml") or first.get("descriptionPlain") or ""
    desc = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", desc)).strip()[:110]
    return ats, slug, len(jobs), desc


def main():
    names = sys.argv[1:] or sys.exit(__doc__)
    tasks = [(name, ats, slug) for name in names for slug in variants(name) for ats in URLS]
    with ThreadPoolExecutor(16) as pool:
        results = pool.map(lambda t: (t[0], probe(t[1], t[2])), tasks)
    hits = {}
    for name, hit in results:
        if hit:
            hits.setdefault(name, []).append(hit)
    for name in names:
        print(f"\n{name}")
        for ats, slug, count, desc in hits.get(name, []):
            print(f'  "{ats}": "{slug}"  ({count} postings)  {desc}')
        if name not in hits:
            print("  no public board found on Greenhouse, Ashby, or Lever")


if __name__ == "__main__":
    main()
