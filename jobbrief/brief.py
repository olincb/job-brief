#!/usr/bin/env python3
"""Deterministic half of the daily job brief. Standard library only.

Subcommands, in the order run.sh calls them:

  fetch   sources.base.json + person sources + sheet dump -> out/candidates.json
  prompt  prompt.md + profile.md + sheet dump + candidates -> out/prompt.txt
  rank    out/prompt.txt -> Gemini API -> out/model.txt
  finish  model output + candidates -> out/brief.md, out/postings_rows.json, out/seen_rows.json
  render  out/brief.md -> out/brief.html, with a link to the tracking sheet
  heartbeat  Runs tab dump -> "send" (and out/heartbeat.html) or "quiet"
  log-run    out/ stats -> out/run_row.json, one row for the Runs tab
  init-sheet create the sheet if config.env has no SHEET_ID, add any missing tab or header,
             and share it with SHARE_WITH if that is set and not already done

Paths are per person: run.sh exports BRIEF_OUT, BRIEF_CONFIG, and BRIEF_PROFILE for
the person under people/<name>/. Without them the defaults point at the repo root.

The model only ranks and writes, in one API call. Fetching, dedup against the
sheet, and building the rows to append all happen here so a run is
reproducible and costs one request.
"""

import argparse
import html
import http.client
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Per-person paths. run.sh exports these; defaults keep single-person use working.
ROOT = Path(__file__).resolve().parent.parent
OUT = Path(os.environ.get("BRIEF_OUT") or ROOT / "out")

# Sheet layout. Postings holds the picks and is edited by hand (status, notes).
# Seen holds every candidate ever shown to the model so it is never re-scored.
POSTINGS_HEADER = ["date_seen", "company", "title", "location", "url", "fit", "reason", "risk", "status", "notes"]
SEEN_HEADER = ["date_seen", "id", "url"]
# Runs is the health log: one row per run. The heartbeat reads it to decide whether
# enough quiet days have passed to say "still here".
RUNS_HEADER = ["date", "candidates", "picks", "skipped_sources", "outcome", "emailed", "model", "tokens"]
SKIPPED = []  # sources that failed to fetch this run, for the Runs row and heartbeat


def get(url, attempts=2):
    """Fetch a URL as text. Large boards occasionally truncate mid-body, so retry once,
    then skip the source rather than failing the whole run."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (job-brief)"})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read().decode()
        except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
            if attempt == attempts - 1:
                print(f"skip {url}: {exc}", file=sys.stderr)
                SKIPPED.append(url)
    return None


def get_json(url):
    body = get(url)
    try:
        return json.loads(body) if body else None
    except json.JSONDecodeError as exc:
        print(f"skip {url}: {exc}", file=sys.stderr)
        return None


def strip_html(text, limit=20000):
    """HTML to text, keeping one newline per block element so bullets stay separable.
    Greenhouse ships its HTML entity-escaped, so unescape before stripping tags."""
    text = html.unescape(text or "")
    text = re.sub(r"</(p|li|div|h\d|tr|br)>|<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\s*\n\s*", "\n", text).strip()[:limit]


# Sentence tiers, most important first. The profile's hard filters are years, remote
# policy, and pay; then explicit requirements; then stack mentions.
SIGNAL_TIERS = [
    re.compile(r"\b\d+\+?\s*(?:-|to)?\s*\d*\s*(?:\+\s*)?years?|remote|hybrid|on-?site|in-?office|time ?zone"
               r"|eligible|authorized|visa|sponsor|salary|compensation|\$\s?\d|\d{2,3}k\b", re.IGNORECASE),
    re.compile(r"experience (?:with|in|building|working|designing|developing|shipping|leading)|proficien|fluen|expert"
               r"|familiar|strong (?:background|understanding|knowledge|experience|skills)|deep (?:understanding|knowledge|experience)"
               r"|degree|\bbs\b|\bms\b|phd|must|required|require\b|nice to have|bonus|preferred|\bplus\b|you have|you.ve"
               r"|you are|you.ll (?:need|bring)|we.re looking for|ideal candidate|track record", re.IGNORECASE),
    re.compile(r"python|rust|\bgo\b|golang|\bjava\b|c\+\+|typescript|kubernetes|\baws\b|gcp|azure|postgres|kafka"
               r"|terraform|distributed|microservice|api\b|sdk|compiler|runtime|linux", re.IGNORECASE),
]
PAY_OR_YEARS = re.compile(r"\d\s*\+?\s*years?|salary|compensation|\$\s?\d", re.IGNORECASE)
BOILERPLATE = re.compile(r"401\(k\)|\bpto\b|paid time off|parental|insurance|equal opportunity|accommodation|discriminat"
                         r"|wellness|dental|vision|stipend|reasonable adjustments|protected", re.IGNORECASE)


def condense(text, intro=300, limit=2400):
    """Keep the opening of a posting plus the sentences that carry requirements, remote
    policy, pay, and stack, filling the budget by importance and then restoring document
    order. Full descriptions run 3k-8k characters and start with company boilerplate, so
    plain truncation hides exactly the lines the profile filters on; heading-based
    extraction fails because benefits sections say "requirements" too."""
    if len(text) <= limit:
        return text
    sentences = [x.strip() for x in re.split(r"\n|(?<=[.!?])\s+", text[intro:]) if len(x.strip()) >= 20]
    ranked = []
    for index, sentence in enumerate(sentences):
        tier = next((t for t, pattern in enumerate(SIGNAL_TIERS) if pattern.search(sentence)), None)
        if tier is None or (BOILERPLATE.search(sentence) and not PAY_OR_YEARS.search(sentence)):
            continue
        ranked.append((tier, index, sentence))
    keep, size = [], 0
    for tier, index, sentence in sorted(ranked):
        if size + len(sentence) <= limit - intro:
            keep.append((index, sentence))
            size += len(sentence) + 1
    if not keep:
        return text[:limit]
    return text[:intro].replace("\n", " ") + " ... " + "\n".join(sentence for _, sentence in sorted(keep))


def posting(ats, company, job_id, title, location, url, posted_at, snippet):
    return {
        "id": f"{ats}:{company}:{job_id}",
        "company": company,
        "title": title,
        "location": location,
        "url": url,
        "posted_at": posted_at,
        "snippet": snippet,
    }


def fetch_greenhouse(slug):
    data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
    for job in (data or {}).get("jobs", []):
        yield posting(
            "greenhouse", slug, job["id"], job["title"],
            (job.get("location") or {}).get("name", ""), job["absolute_url"],
            job.get("first_published") or job.get("updated_at"), strip_html(job.get("content")),
        )


def fetch_lever(slug):
    data = get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    for job in data or []:
        created = datetime.fromtimestamp(job["createdAt"] / 1000, tz=timezone.utc).isoformat()
        yield posting(
            "lever", slug, job["id"], job["text"],
            (job.get("categories") or {}).get("location", ""), job["hostedUrl"],
            created, strip_html(job.get("descriptionPlain") or job.get("description")),
        )


def fetch_ashby(slug):
    data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true")
    for job in (data or {}).get("jobs", []):
        yield posting(
            "ashby", slug, job["id"], job["title"],
            job.get("location", ""), job["jobUrl"],
            job.get("publishedAt"), strip_html(job.get("descriptionPlain") or job.get("descriptionHtml")),
        )


def fetch_climatebase(query):
    """Climatebase has no API, but its search page is server-rendered with the
    result set embedded as Next.js page data. One query returns up to 100 jobs;
    pagination parameters are ignored, so recency and the title filter do the narrowing."""
    page = get("https://climatebase.org/jobs?" + urllib.parse.urlencode({"q": query}))
    match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', page or "", re.S)
    if not match:
        print(f"skip climatebase {query!r}: no page data", file=sys.stderr)
        return
    for job in json.loads(match.group(1))["props"]["pageProps"]["jobs"]:
        location = ", ".join(job.get("locations") or []) or ", ".join(job.get("remote_preferences") or [])
        salary = f"{job.get('salary_from')}-{job.get('salary_to')} {job.get('salary_period')}" if job.get("salary_from") else ""
        snippet = " | ".join(filter(None, [
            ", ".join(job.get("sectors") or []), ", ".join(job.get("job_types") or []), salary,
            strip_html(job.get("employer_short_description"), 300),  # list page has no job description; enrich_climatebase fetches it
        ]))
        yield posting(
            "climatebase", job["name_of_employer"], job["id"], job["title"], location,
            f"https://climatebase.org/job/{job['id']}", job.get("activation_date"), snippet,
        )


HN_SEARCH = "https://hn.algolia.com/api/v1/search"


def fetch_hackernews(_):
    """Top-level comments on the two most recent monthly "Ask HN: Who is hiring?" threads.
    Posts follow "Company | Role | Location | ..." on their first line, which becomes the
    title so the title filters still apply. Only posts mentioning remote are kept."""
    threads = get_json(HN_SEARCH + "_by_date?query=%22Who%20is%20hiring%22&tags=story,author_whoishiring&hitsPerPage=2")
    for thread in (threads or {}).get("hits", []):
        story_id = thread["objectID"]
        comments = get_json(f"{HN_SEARCH}?tags=comment,story_{story_id}&hitsPerPage=1000")
        for c in (comments or {}).get("hits", []):
            if str(c.get("parent_id")) != story_id or not c.get("comment_text"):
                continue
            text = strip_html(c["comment_text"])
            if not re.search(r"\bremote\b", text, re.IGNORECASE):
                continue
            first_line = strip_html(c["comment_text"].split("<p>")[0], 200)
            company = first_line.split("|")[0].strip()[:60]
            yield {**posting(
                "hn", "whoishiring", c["objectID"], first_line, "see post",
                f"https://news.ycombinator.com/item?id={c['objectID']}", c["created_at"], text,
            ), "company": company}


US_REMOTE = re.compile(r"worldwide|anywhere|global|united states|\busa?\b|americas|north america", re.IGNORECASE)


def fetch_remoteok(_):
    """The 100 most recent RemoteOK postings. Everything on the site is remote, so the
    only filter is that the location allows the US."""
    data = get_json("https://remoteok.com/api")
    for job in (data or [])[1:]:  # element 0 is a legal notice
        location = job.get("location") or ""
        if location.strip() and not US_REMOTE.search(location):
            continue
        salary = f"{job['salary_min']}-{job['salary_max']} USD" if job.get("salary_min") not in (None, "0", 0) else ""
        yield posting(
            "remoteok", job.get("company", ""), job["id"], job.get("position", ""), location or "Worldwide",
            job["url"], job.get("date"), " | ".join(filter(None, [salary, strip_html(job.get("description"))])),
        )


def fetch_himalayas(_):
    """Himalayas remote job feed, newest first, 20 per page, cursor paginated. Walk pages
    until postings are older than a week; the lookback filter does the rest."""
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).timestamp()
    cursor = ""
    for _page in range(15):
        data = get_json("https://himalayas.app/jobs/api?limit=20" + (f"&cursor={cursor}" if cursor else ""))
        if not data:
            return
        for job in data.get("jobs", []):
            restrictions = job.get("locationRestrictions") or []
            if restrictions and not any(US_REMOTE.search(r) for r in restrictions):
                continue
            posted = datetime.fromtimestamp(int(job["pubDate"]), tz=timezone.utc).isoformat()
            salary = f"{job['minSalary']}-{job['maxSalary']} {job.get('currency', '')}" if job.get("minSalary") else ""
            yield posting(
                "himalayas", job.get("companyName", ""), job["guid"].rstrip("/").rsplit("/", 1)[-1], job["title"],
                ", ".join(restrictions) or "Worldwide", job["applicationLink"], posted,
                " | ".join(filter(None, [", ".join(job.get("seniority") or []), salary, strip_html(job.get("description"))])),
            )
        if not data.get("jobs") or int(data["jobs"][-1]["pubDate"]) < week_ago:
            return
        cursor = data.get("nextCursor") or ""
        if not cursor:
            return


def fetch_apple(_):
    """Every US posting Apple marks home-office eligible, newest first. The search page
    embeds its results as router hydration data, 20 per page. Apple has only a couple of
    dozen such roles at any time, so this walks the whole set and the title filters do
    the narrowing."""
    for page in range(1, 6):
        params = urllib.parse.urlencode({"location": "united-states-USA", "homeOffice": "true", "sort": "newest", "page": page})
        html_page = get("https://jobs.apple.com/en-us/search?" + params)
        match = re.search(r"window\.__staticRouterHydrationData\s*=\s*JSON\.parse\((\".*?\")\);", html_page or "", re.S)
        if not match:
            return
        results = json.loads(json.loads(match.group(1)))["loaderData"]["search"]["searchResults"]
        for job in results:
            yield posting(
                "apple", "Apple", job["positionId"], job["postingTitle"],
                "Remote (home office) - " + ", ".join(loc.get("name", "") for loc in job.get("locations", [])),
                f"https://jobs.apple.com/en-us/details/{job['positionId']}/{job['transformedPostingTitle']}",
                job.get("postDateInGMT"), (job.get("team") or {}).get("teamName", "") + " | " + strip_html(job.get("jobSummary")),
            )
        if len(results) < 20:
            return


def enrich_climatebase(job):
    page = get(job["url"])
    match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', page or "", re.S)
    if not match:
        return None
    data = json.loads(match.group(1))["props"]["pageProps"]["data"]
    apply_link = data.get("how_to_apply") or ""
    return job["snippet"] + " | " + strip_html(data.get("description")) + (f" | apply: {apply_link}" if apply_link else "")


def enrich_apple(job):
    page = get(job["url"])
    match = re.search(r"window\.__staticRouterHydrationData\s*=\s*JSON\.parse\((\".*?\")\);", page or "", re.S)
    if not match:
        return None
    data = json.loads(json.loads(match.group(1)))["loaderData"]["jobDetails"]["jobsData"]
    parts = [data.get("description"), "Minimum: " + (data.get("minimumQualifications") or ""),
             "Preferred: " + (data.get("preferredQualifications") or ""), data.get("responsibilities")]
    return " | ".join(strip_html(part) for part in parts if part)


# Sources whose list call lacks the job description. Run only on candidates that survive
# dedup and the title filters, so this is a handful of requests per run.
ENRICHERS = {"climatebase": enrich_climatebase, "apple": enrich_apple}


# Company-board fetchers take an ATS slug. climatebase takes a search query.
# hackernews, remoteok, himalayas, and apple are single feeds and ignore their value.
FETCHERS = {
    "greenhouse": fetch_greenhouse, "lever": fetch_lever, "ashby": fetch_ashby,
    "climatebase": fetch_climatebase, "hackernews": fetch_hackernews,
    "remoteok": fetch_remoteok, "himalayas": fetch_himalayas, "apple": fetch_apple,
}


def load_sheet_rows(path):
    """A `gws sheets spreadsheets values get` dump -> list of dicts keyed by the header row."""
    if not Path(path).exists():
        return []
    values = json.loads(Path(path).read_text()).get("values", [])
    if len(values) < 2:
        return []
    header, rows = values[0], values[1:]
    return [dict(zip(header, row + [""] * (len(header) - len(row)))) for row in rows]


def is_recent(iso, lookback_days):
    if not iso:
        return True
    posted = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return posted >= datetime.now(timezone.utc) - timedelta(days=lookback_days)


def cmd_fetch(args):
    base = json.loads(Path(args.sources).read_text())
    person = json.loads(Path(args.person_sources).read_text()) if args.person_sources else {}
    # Shared boards plus the person's own; the person's keyword filters win.
    sources = {ats: list(dict.fromkeys(base.get(ats, []) + person.get(ats, []))) for ats in FETCHERS}
    title_ok = re.compile("|".join(person.get("title_filter") or base.get("title_filter", [])) or ".", re.IGNORECASE)
    title_bad = re.compile("|".join(person.get("title_exclude") or base.get("title_exclude", [])) or "(?!)", re.IGNORECASE)
    seen = {row["url"] for row in load_sheet_rows(args.seen)}
    candidates = {}
    for ats, fetcher in FETCHERS.items():
        for slug in sources.get(ats, []):
            for job in fetcher(slug):
                if job["url"] in seen or not is_recent(job["posted_at"], args.lookback_days):
                    continue
                if title_ok.search(job["title"]) and not title_bad.search(job["title"]):
                    candidates[job["id"]] = job  # overlapping queries can return the same posting
    candidates = list(candidates.values())
    for job in candidates:
        enrich = ENRICHERS.get(job["id"].split(":")[0])
        if enrich:
            job["snippet"] = enrich(job) or job["snippet"]
        job["snippet"] = condense(job["snippet"])
    (OUT / "candidates.json").write_text(json.dumps(candidates, indent=1))
    (OUT / "fetch_stats.json").write_text(json.dumps({"new_candidates": len(candidates), "skipped_sources": SKIPPED}))
    print(f"{len(candidates)} new candidates, {len(SKIPPED)} sources skipped", file=sys.stderr)


def cmd_prompt(args):
    pipeline = load_sheet_rows(args.postings)
    candidates = json.loads((OUT / "candidates.json").read_text())
    text = "\n\n".join([
        Path(args.prompt).read_text(),
        f"Maximum picks: {args.max_picks}",
        "## Candidate profile\n\n" + Path(args.profile).read_text(),
        "## Pipeline\n\n" + json.dumps(pipeline, indent=1),
        "## Candidates\n\n" + json.dumps(candidates, indent=1),
    ])
    (OUT / "prompt.txt").write_text(text)


GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


RETRYABLE = (429, 500, 503)


def call_gemini(model, body, api_key, attempts):
    """Try one model up to `attempts` times. Returns the parsed response, or None when
    every attempt hit a retryable failure. Non-retryable errors exit immediately.
    Backoff starts at 30 seconds, doubles, and caps at five minutes, so four attempts
    span about three and a half minutes: enough to ride out a brief blip before the
    fallback model takes over."""
    req = urllib.request.Request(
        GEMINI_URL.format(model=model), data=body,
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
    )
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            if exc.code not in RETRYABLE:
                raise SystemExit(f"{model}: HTTP {exc.code}: {detail}")
            problem = f"HTTP {exc.code}: {detail}"
        except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
            problem = f"unreachable: {exc}"
        if attempt < attempts - 1:
            wait = min(30 * 2 ** attempt, 300)
            print(f"{model} attempt {attempt + 1}/{attempts} failed ({problem}); retrying in {wait}s", file=sys.stderr)
            time.sleep(wait)
        else:
            print(f"{model} attempt {attempt + 1}/{attempts} failed ({problem}); giving up on this model", file=sys.stderr)
    return None


def cmd_rank(args):
    """One generateContent call, with the retry budget split between the primary model and
    a fallback. 503 "model is overloaded" clusters on whichever model launched most
    recently and hits paid tiers too, so an older Flash is the reliable escape hatch."""
    api_key = os.environ.get("GEMINI_API_KEY") or sys.exit("GEMINI_API_KEY is not set (see config.env)")
    body = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": (OUT / "prompt.txt").read_text()}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
    }).encode()
    models = [args.model] + ([args.fallback_model] if args.fallback_model and args.fallback_model != args.model else [])
    per_model = max(1, args.retries // len(models))
    data = None
    for model in models:
        data = call_gemini(model, body, api_key, per_model)
        if data:
            break
    if not data:
        raise SystemExit(f"all {args.retries} attempts failed across {', '.join(models)}")
    text = "".join(part.get("text", "") for part in data["candidates"][0]["content"]["parts"])
    (OUT / "model.txt").write_text(text)
    usage = data.get("usageMetadata", {})
    (OUT / "rank_stats.json").write_text(json.dumps({"model": model, "tokens": usage.get("totalTokenCount", "")}))
    print(f"model {model}: {usage.get('promptTokenCount')} in, {usage.get('candidatesTokenCount')} out, "
          f"{usage.get('thoughtsTokenCount', 0)} thinking", file=sys.stderr)


def parse_model_json(raw):
    """Tolerate a fenced block or stray prose around the object."""
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        raise SystemExit(f"model output had no JSON object:\n{raw[:500]}")
    return json.loads(raw[start:end + 1])


def cmd_finish(args):
    today = datetime.now().strftime("%Y-%m-%d")
    result = parse_model_json(Path(args.model_output).read_text())
    candidates = {c["id"]: c for c in json.loads((OUT / "candidates.json").read_text())}

    postings_rows = []
    for pick in result.get("picks", []):
        cand = candidates.get(pick["id"])
        if cand is None:
            print(f"model invented id {pick['id']}, dropping", file=sys.stderr)
            continue
        postings_rows.append([
            today, cand["company"], cand["title"], cand["location"], cand["url"],
            pick.get("fit", ""), pick.get("reason", ""), pick.get("risk", ""), "", "",
        ])
    seen_rows = [[today, c["id"], c["url"]] for c in candidates.values()]

    (OUT / "brief.md").write_text(result["brief_markdown"])
    (OUT / "postings_rows.json").write_text(json.dumps({"values": postings_rows}))
    (OUT / "seen_rows.json").write_text(json.dumps({"values": seen_rows}))
    print(f"{len(postings_rows)} picks, {len(seen_rows)} marked seen", file=sys.stderr)


STYLE = {
    "body": "font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;font-size:15px;line-height:1.45;color:#1c1c1c;max-width:720px;margin:0 auto;padding:16px",
    "h1": "font-size:20px;margin:24px 0 8px", "h2": "font-size:17px;margin:22px 0 6px", "h3": "font-size:15px;margin:18px 0 4px",
    "p": "margin:0 0 10px", "li": "margin:0 0 8px", "a": "color:#0b57d0",
    "footer": "margin-top:28px;padding-top:12px;border-top:1px solid #ddd;font-size:13px;color:#555",
}


def inline_markdown(text):
    """Bold, links, and bare URLs. Everything else is escaped."""
    text = html.escape(text, quote=False)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", rf'<a style="{STYLE["a"]}" href="\2">\1</a>', text)
    text = re.sub(r"(?<![\"'>=])(https?://[^\s<)]+)", rf'<a style="{STYLE["a"]}" href="\1">\1</a>', text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    return text


def markdown_to_html(markdown):
    """Enough Markdown for the brief: headings, ordered and bulleted lists, paragraphs,
    bold, links. Line breaks inside a list item or paragraph become <br> so the
    per-pick lines stay stacked. Blank lines end a paragraph but not a list, since the
    model separates numbered picks with blank lines. A list that does start fresh keeps
    the number the model wrote. Inline styles because mail clients strip <style>."""
    out, block, kind, list_start = [], [], None, 1

    def flush():
        nonlocal block, kind
        if not block:
            return
        if kind in ("ol", "ul"):
            items = "".join(f'<li style="{STYLE["li"]}">{"<br>".join(map(inline_markdown, item))}</li>' for item in block)
            start_attr = f' start="{list_start}"' if kind == "ol" and list_start != 1 else ""
            out.append(f'<{kind}{start_attr} style="padding-left:22px;margin:0 0 12px">{items}</{kind}>')
        else:
            out.append(f'<p style="{STYLE["p"]}">{"<br>".join(map(inline_markdown, block))}</p>')
        block, kind = [], None

    for raw in markdown.splitlines():
        line = raw.rstrip()
        heading = re.match(r"^(#{1,3})\s+(.*)", line)
        item = re.match(r"^(\d+)\.\s+(.*)|^[-*]\s+(.*)", line)
        if not line:
            if kind == "p":
                flush()
        elif heading:
            flush()
            tag = f"h{len(heading.group(1))}"
            out.append(f'<{tag} style="{STYLE[tag]}">{inline_markdown(heading.group(2))}</{tag}>')
        elif item:
            new_kind = "ol" if item.group(1) else "ul"
            if kind != new_kind:
                flush()
                list_start = int(item.group(1)) if new_kind == "ol" else 1
            kind = new_kind
            block.append([item.group(2) if new_kind == "ol" else item.group(3)])
        elif kind in ("ol", "ul") and raw.startswith((" ", "\t")):
            block[-1].append(line.strip())  # indented continuation of the current item
        else:
            if kind in ("ol", "ul"):
                flush()
            kind = "p"
            block.append(line)
    flush()
    return "\n".join(out)


def backlog_html(postings_path):
    """One line: how many picks already in the sheet still have a blank status, and how
    old the oldest is. Computed here, not by the model, so the number is exact."""
    rows = load_sheet_rows(postings_path)
    if not rows:
        return ""
    pending = [r for r in rows if not r.get("status", "").strip()]
    if not pending:
        line = "Every pick in the sheet has a status."
    else:
        oldest = min((r["date_seen"] for r in pending if r.get("date_seen")), default="")
        line = f"<strong>{len(pending)} earlier picks still have no status</strong>, oldest from {oldest}."
    return f'<p style="{STYLE["footer"]}">{line}</p>'


def cmd_render(args):
    body = markdown_to_html((OUT / "brief.md").read_text()) + backlog_html(args.postings)
    footer = ""
    if args.sheet_id:
        url = f"https://docs.google.com/spreadsheets/d/{args.sheet_id}"
        footer = (f'<div style="{STYLE["footer"]}">Tracking sheet: <a style="{STYLE["a"]}" href="{url}">{url}</a>'
                  f"<br>Mark status on the Postings tab to change what the next brief says.</div>")
    (OUT / "brief.html").write_text(f'<div style="{STYLE["body"]}">{body}{footer}</div>')


def cmd_heartbeat(args):
    """Decide whether a quiet run should still say hello. Sends when no email has gone
    out in `--days` days according to the Runs tab, so a single quiet day is silent but
    silence never lasts long enough to be mistaken for breakage."""
    today = datetime.now().date()
    emailed = [row["date"] for row in load_sheet_rows(args.runs) if row.get("emailed") == "yes" and row.get("date")]
    last = max((datetime.fromisoformat(d).date() for d in emailed), default=None)
    quiet_days = (today - last).days if last else None
    if last and quiet_days < args.days:
        print("quiet")
        return
    stats = json.loads((OUT / "fetch_stats.json").read_text()) if (OUT / "fetch_stats.json").exists() else {}
    skipped = stats.get("skipped_sources", [])
    since = f"{quiet_days} days since the last email" if last else "no email on record yet"
    sources = f"{len(skipped)} sources failed to fetch:<br>" + "<br>".join(html.escape(u) for u in skipped) if skipped else "all sources reachable"
    body = (f'<div style="{STYLE["body"]}"><p style="{STYLE["p"]}">Still running. {since}, nothing new to show today.</p>'
            f'<p style="{STYLE["p"]}">Today: {stats.get("new_candidates", 0)} new candidates after dedup, {sources}.</p>'
            f'{backlog_html(args.postings)}</div>')
    (OUT / "heartbeat.html").write_text(body)
    print("send")


def cmd_log_run(args):
    stats = json.loads((OUT / "fetch_stats.json").read_text()) if (OUT / "fetch_stats.json").exists() else {}
    picks_file = OUT / "postings_rows.json"
    picks = len(json.loads(picks_file.read_text())["values"]) if picks_file.exists() else 0
    rank_file = OUT / "rank_stats.json"
    rank = json.loads(rank_file.read_text()) if rank_file.exists() else {}
    row = [datetime.now().strftime("%Y-%m-%d"), stats.get("new_candidates", ""), picks,
           len(stats.get("skipped_sources", [])), args.outcome, args.emailed, rank.get("model", ""), rank.get("tokens", "")]
    (OUT / "run_row.json").write_text(json.dumps({"values": [row]}))


TABS = {"Postings": POSTINGS_HEADER, "Seen": SEEN_HEADER, "Runs": RUNS_HEADER}
CONFIG = Path(os.environ.get("BRIEF_CONFIG") or ROOT / "config.env")


def gws(*args):
    """Run a gws command and parse its JSON output."""
    result = subprocess.run(["gws", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"gws {' '.join(args[:3])} failed:\n{result.stderr.strip()}")
    return json.loads(result.stdout) if result.stdout.strip() else {}


def read_config_value(key):
    if not CONFIG.exists():
        return ""
    match = re.search(rf"^{key}=([^#\n]*)", CONFIG.read_text(), re.MULTILINE)
    return match.group(1).strip().strip('"') if match else ""


def write_config_value(key, value):
    text = CONFIG.read_text() if CONFIG.exists() else ""
    if re.search(rf"^{key}=", text, re.MULTILINE):
        text = re.sub(rf"^{key}=.*$", f"{key}={value}", text, flags=re.MULTILINE)
    else:
        if text and not text.endswith("\n"):
            text += "\n"  # hand-edited files often lack a final newline
        text += f"{key}={value}\n"
    CONFIG.write_text(text)


def cmd_init_sheet(args):
    """Idempotent. Creates the spreadsheet only when config.env has no SHEET_ID; on every
    run adds whichever of the three tabs are missing and writes any missing header row.
    Safe to rerun after upgrading, e.g. when a new tab is introduced."""
    sheet_id = args.sheet_id or read_config_value("SHEET_ID")
    if not sheet_id:
        # Title by person when config lives under people/<name>/, else the single-person name.
        person = CONFIG.parent.name
        title = f"Job Brief - {person}" if CONFIG.parent.parent.name == "people" else "Job Brief"
        created = gws("sheets", "spreadsheets", "create", "--json", json.dumps({
            "properties": {"title": title},
            "sheets": [{"properties": {"title": tab}} for tab in TABS],
        }))
        sheet_id = created["spreadsheetId"]
        write_config_value("SHEET_ID", sheet_id)
        print(f"created spreadsheet {sheet_id} and wrote SHEET_ID to config.env")

    meta = gws("sheets", "spreadsheets", "get", "--params", json.dumps({"spreadsheetId": sheet_id}))
    existing = {sheet["properties"]["title"] for sheet in meta.get("sheets", [])}
    for tab in TABS:
        if tab not in existing:
            gws("sheets", "spreadsheets", "batchUpdate", "--params", json.dumps({"spreadsheetId": sheet_id}),
                "--json", json.dumps({"requests": [{"addSheet": {"properties": {"title": tab}}}]}))
            print(f"added tab {tab}")

    for tab, header in TABS.items():
        first_row = gws("sheets", "spreadsheets", "values", "get",
                        "--params", json.dumps({"spreadsheetId": sheet_id, "range": f"{tab}!1:1"})).get("values", [[]])
        if first_row and first_row[0] == header:
            continue
        # Row 1 is ours alone, so rewriting it is safe. This is how a new column reaches
        # an existing sheet; older rows simply have a blank in that column.
        gws("sheets", "spreadsheets", "values", "update",
            "--params", json.dumps({"spreadsheetId": sheet_id, "range": f"{tab}!A1", "valueInputOption": "RAW"}),
            "--json", json.dumps({"values": [header]}))
        print(f"wrote header row for {tab}" if not first_row[0] else f"updated header row for {tab}")
    share_with = read_config_value("SHARE_WITH")
    if share_with:
        share_sheet(sheet_id, share_with)
    print(f"ready: https://docs.google.com/spreadsheets/d/{sheet_id}")


def share_sheet(sheet_id, email):
    """Give one Google account edit access, once. The sheet is owned by the gws login, so
    the person whose brief it is needs this to set status and notes. Requires the
    drive.file scope, which covers files this OAuth client created."""
    listing = gws("drive", "permissions", "list", "--params",
                  json.dumps({"fileId": sheet_id, "fields": "permissions(emailAddress,role)"}))
    for perm in listing.get("permissions", []):
        if (perm.get("emailAddress") or "").lower() == email.lower():
            if perm.get("role") in ("writer", "owner"):
                return
            break
    gws("drive", "permissions", "create", "--params", json.dumps({"fileId": sheet_id, "sendNotificationEmail": True}),
        "--json", json.dumps({"type": "user", "role": "writer", "emailAddress": email}))
    print(f"shared with {email} as editor")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("fetch")
    p.add_argument("--sources", default="sources.base.json", help="shared board list")
    p.add_argument("--person-sources", default="", help="this person's extra boards and keyword filters")
    p.add_argument("--seen", default=str(OUT / "seen.json"), help="gws dump of the Seen tab")
    p.add_argument("--lookback-days", type=int, default=3)
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("prompt")
    p.add_argument("--prompt", default="prompt.md")
    p.add_argument("--profile", default=os.environ.get("BRIEF_PROFILE") or "profile.md")
    p.add_argument("--postings", default=str(OUT / "postings.json"), help="gws dump of the Postings tab")
    p.add_argument("--max-picks", type=int, default=10)
    p.set_defaults(func=cmd_prompt)

    p = sub.add_parser("rank")
    p.add_argument("--model", default=os.environ.get("GEMINI_MODEL") or "gemini-3.8-flash")
    p.add_argument("--fallback-model", default=os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash"))
    p.add_argument("--retries", type=int, default=int(os.environ.get("GEMINI_RETRIES") or 8),
                   help="total attempts, split evenly between the primary and fallback models")
    p.set_defaults(func=cmd_rank)

    p = sub.add_parser("finish")
    p.add_argument("--model-output", default=str(OUT / "model.txt"))
    p.set_defaults(func=cmd_finish)

    p = sub.add_parser("render")
    p.add_argument("--sheet-id", default=os.environ.get("SHEET_ID", ""))
    p.add_argument("--postings", default=str(OUT / "postings.json"), help="gws dump of the Postings tab")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("heartbeat")
    p.add_argument("--runs", default=str(OUT / "runs.json"), help="gws dump of the Runs tab")
    p.add_argument("--postings", default=str(OUT / "postings.json"), help="gws dump of the Postings tab")
    p.add_argument("--days", type=int, default=4)
    p.set_defaults(func=cmd_heartbeat)

    p = sub.add_parser("log-run")
    p.add_argument("--outcome", required=True, choices=["sent", "quiet", "heartbeat", "failed"])
    p.add_argument("--emailed", required=True, choices=["yes", "no"])
    p.set_defaults(func=cmd_log_run)

    p = sub.add_parser("init-sheet")
    p.add_argument("--sheet-id", default="", help="override the SHEET_ID in config.env")
    p.set_defaults(func=cmd_init_sheet)

    OUT.mkdir(exist_ok=True)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
