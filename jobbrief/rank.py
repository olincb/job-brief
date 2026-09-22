"""The ranking stage around the model call: assembling the prompt from the template,
profile, pipeline, and candidates, and turning the answer into the brief and sheet rows."""

import json
import sys
from collections import Counter


def build_prompt(template, profile, pipeline, candidates, max_picks):
    """The single request body's text: instructions, then the three blocks the template
    names, in the order it names them."""
    return "\n\n".join([
        template,
        f"Maximum picks: {max_picks}",
        "## Candidate profile\n\n" + profile,
        "## Pipeline\n\n" + json.dumps(pipeline, indent=1),
        "## Candidates\n\n" + json.dumps(candidates, indent=1),
    ])


def finish(result, candidates, today):
    """The model's answer as the brief, the rows to append, and picks per source for the
    Runs row. A pick whose id is not a real candidate is dropped rather than trusted."""
    by_id = {c["id"]: c for c in candidates}
    postings_rows = []
    per_source = Counter()
    for pick in result.get("picks", []):
        cand = by_id.get(pick["id"])
        if cand is None:
            print(f"model invented id {pick['id']}, dropping", file=sys.stderr)
            continue
        postings_rows.append([
            today, cand["company"], cand["title"], cand["location"], cand["url"],
            pick.get("fit", ""), pick.get("reason", ""), pick.get("risk", ""), "", "",
        ])
        per_source[cand["id"].split(":", 1)[0]] += 1
    seen_rows = [[today, c["id"], c["url"]] for c in candidates]
    sources = " ".join(f"{ats}:{n}" for ats, n in sorted(per_source.items(), key=lambda item: (-item[1], item[0])))
    return result["brief_markdown"], postings_rows, seen_rows, sources
