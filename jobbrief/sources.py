"""Where postings come from. One generator function per source, each yielding postings in
the common shape from posting(); a source that is unreachable is skipped, not fatal. Also
the per-user selection over the fetched pool and condense, which trims a posting to the
lines a profile filters on."""

import email.utils
import html
import http.client
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree


SKIPPED = []  # sources that failed to fetch this run, for the Runs row and heartbeat


def get(url, attempts=2, headers=None):
    """Fetch a URL as text. Large boards occasionally truncate mid-body, so retry once,
    then skip the source rather than failing the whole run. A 404 is a board that does not
    exist, so it is skipped without the retry."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (job-brief)", **(headers or {})})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read().decode()
        except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
            if attempt == attempts - 1 or getattr(exc, "code", None) == 404:
                print(f"skip {url}: {exc}", file=sys.stderr)
                SKIPPED.append(url)
                return None


def get_json(url, headers=None):
    body = get(url, headers=headers)
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
    # Stack mentions: the software default, used when a user's Settings vocabulary row is blank.
    re.compile(r"python|rust|\bgo\b|golang|\bjava\b|c\+\+|typescript|kubernetes|\baws\b|gcp|azure|postgres|kafka"
               r"|terraform|distributed|microservice|api\b|sdk|compiler|runtime|linux", re.IGNORECASE),
]
PAY_OR_YEARS = re.compile(r"\d\s*\+?\s*years?|salary|compensation|\$\s?\d", re.IGNORECASE)
BOILERPLATE = re.compile(r"401\(k\)|\bpto\b|paid time off|parental|insurance|equal opportunity|accommodation|discriminat"
                         r"|wellness|dental|vision|stipend|reasonable adjustments|protected", re.IGNORECASE)


def condense(text, intro=300, limit=2400, vocabulary=None):
    """Keep the opening of a posting plus the sentences that carry requirements, remote
    policy, pay, and stack, filling the budget by importance and then restoring document
    order. Full descriptions run 3k-8k characters and start with company boilerplate, so
    plain truncation hides exactly the lines the profile filters on; heading-based
    extraction fails because benefits sections say "requirements" too. A non-empty
    `vocabulary` of literal terms stands in for the software stack tier."""
    if len(text) <= limit:
        return text
    tiers = SIGNAL_TIERS
    if vocabulary:
        # Lookarounds rather than \b so a term ending in a symbol, like C++, still matches.
        # Postings write the typographic apostrophe, so a straight one in a term must match either.
        escaped = (re.escape(term).replace("'", "['’]") for term in vocabulary)
        terms = "|".join(rf"(?<!\w){term}(?!\w)" for term in escaped)
        tiers = SIGNAL_TIERS[:-1] + [re.compile(terms, re.IGNORECASE)]
    sentences = [x.strip() for x in re.split(r"\n|(?<=[.!?])\s+", text[intro:]) if len(x.strip()) >= 20]
    ranked = []
    for index, sentence in enumerate(sentences):
        tier = next((t for t, pattern in enumerate(tiers) if pattern.search(sentence)), None)
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


def fetch_neogov(agency):
    """One agency's board on governmentjobs.com, from the endpoint behind the careers page's
    map view: every open posting in one response, a row per location, the description cut
    to its opening paragraph."""
    # The endpoint answers 404 unless the request carries the header the page's own script sends.
    data = get_json(f"https://www.governmentjobs.com/careers/home/loadJobsOnMaps?agency={agency}",
                    headers={"X-Requested-With": "XMLHttpRequest"})
    for job in (data or {}).get("jobList", []):
        posted = datetime.strptime(job["PostingDate"], "%m/%d/%y").replace(tzinfo=timezone.utc).isoformat()
        yield posting(
            "neogov", agency, job["ID"], job["Classification"], job.get("Location") or "",
            f"https://www.governmentjobs.com/careers/{agency}/jobs/{job['ID']}/{job['JobTitle']}", posted,
            " | ".join(filter(None, [job.get("JobType"), job.get("SalaryInfo"), strip_html(job.get("FullDescription"))])),
        )


def wordpress_url(board):
    """The REST URL for a `sources.base.json` wordpress entry, `site/post_type` with an
    optional `?search=term`."""
    board = urllib.parse.urlsplit("//" + board)
    params = {**dict(urllib.parse.parse_qsl(board.query)), "per_page": 20, "orderby": "date", "order": "desc",
              "_fields": "id,title,link,date_gmt,content"}
    return f"https://{board.netloc}/wp-json/wp/v2{board.path}?" + urllib.parse.urlencode(params)


def fetch_wordpress(board):
    """The newest 20 posts of one job post type on a WordPress site, labelled with the
    site's first DNS label so picks per source name the board. Josh's Water Jobs posts
    about fifty a day worldwide, so it is searched rather than read whole. Titles name no
    organization and location is free text in the body, so the description carries both."""
    for job in get_json(wordpress_url(board)) or []:
        yield posting(
            board.split(".")[0], "", job["id"], strip_html(job["title"]["rendered"]), "see posting",
            job["link"], job["date_gmt"] + "+00:00", strip_html(job["content"]["rendered"]),
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


def fetch_weworkremotely(category):
    """The newest postings in one We Work Remotely category's RSS feed, whose titles read
    "Company: Title"."""
    feed = get(f"https://weworkremotely.com/categories/{category}.rss")
    if not feed:
        return
    for item in ElementTree.fromstring(feed).iter("item"):
        head, sep, tail = item.findtext("title").partition(": ")
        company, title = (head, tail) if sep else ("", head)
        link = item.findtext("link")
        yield posting(
            "weworkremotely", company, item.findtext("guid") or link, title, item.findtext("region") or "Remote",
            link, email.utils.parsedate_to_datetime(item.findtext("pubDate")).isoformat(),
            strip_html(item.findtext("description")),
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


def enrich_neogov(job):
    page = get(job["url"])
    match = re.search(r'<script type="application/ld\+json">(.*?)</script>', page or "", re.S)
    if not match:
        return None
    # The list snippet ends with the description's opening paragraph, which the full description repeats.
    return job["snippet"].rsplit(" | ", 1)[0] + " | " + strip_html(json.loads(match.group(1)).get("description"))


# Sources whose list call lacks the job description. Run only on candidates that survive
# dedup and the title filters, so this is a handful of requests per run.
ENRICHERS = {"climatebase": enrich_climatebase, "apple": enrich_apple, "neogov": enrich_neogov}


# Company-board fetchers take an ATS slug, neogov an agency slug, climatebase a search query,
# wordpress a site and post type with an optional search term, and weworkremotely a category.
# hackernews, remoteok, himalayas, and apple are single feeds and ignore their value.
FETCHERS = {
    "greenhouse": fetch_greenhouse, "lever": fetch_lever, "ashby": fetch_ashby, "neogov": fetch_neogov,
    "climatebase": fetch_climatebase, "wordpress": fetch_wordpress, "hackernews": fetch_hackernews,
    "remoteok": fetch_remoteok, "weworkremotely": fetch_weworkremotely, "himalayas": fetch_himalayas,
    "apple": fetch_apple,
}


def is_recent(iso, lookback_days):
    if not iso:
        return True
    posted = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return posted >= datetime.now(timezone.utc) - timedelta(days=lookback_days)


# More than this many condensed postings crowds out the profile and pipeline in the model request.
CANDIDATE_CAP = 150
# A board posting hundreds of matching titles a week would otherwise fill the cap by itself.
BOARD_SHARE = CANDIDATE_CAP // 3


def select_candidates(postings, seen_urls, title_filter, title_exclude, lookback_days):
    """One user's view of the pool as `(candidates, capped)`: unseen, recent, and passing
    their title filters. A title must match one filter pattern and no exclude pattern,
    case-insensitively. Overlapping sources can return the same posting, so the result is
    keyed by id. Over the cap the newest are kept, at most `BOARD_SHARE` from one board,
    the board being the id's first two fields."""
    title_ok = re.compile("|".join(title_filter or []) or ".", re.IGNORECASE)
    title_bad = re.compile("|".join(title_exclude or []) or "(?!)", re.IGNORECASE)
    candidates = {}
    for job in postings:
        if job["url"] in seen_urls or not is_recent(job["posted_at"], lookback_days):
            continue
        if title_ok.search(job["title"]) and not title_bad.search(job["title"]):
            candidates[job["id"]] = job
    selected = list(candidates.values())
    if len(selected) <= CANDIDATE_CAP:
        return selected, False
    # ISO timestamps order correctly as strings, and a posting with no date sorts oldest.
    selected.sort(key=lambda job: job["posted_at"] or "", reverse=True)
    per_board = Counter()
    kept = []
    for job in selected:
        board = ":".join(job["id"].split(":", 2)[:2])
        if per_board[board] < BOARD_SHARE:
            per_board[board] += 1
            kept.append(job)
    return kept[:CANDIDATE_CAP], True


def enrich(candidates, vocabulary=None):
    """Fill in descriptions for sources whose list call lacks one, then condense every
    snippet to the lines the profile filters on, keyed on the user's `vocabulary` when
    given. Runs after selection so it is a handful of requests per run."""
    for job in candidates:
        fetch_description = ENRICHERS.get(job["id"].split(":")[0])
        if fetch_description:
            job["snippet"] = fetch_description(job) or job["snippet"]
        job["snippet"] = condense(job["snippet"], vocabulary=vocabulary)
    return candidates
