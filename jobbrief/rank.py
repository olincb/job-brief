"""The ranking stage around the model call: assembling the prompt from the template,
profile, pipeline, and candidates, and turning the answer into the brief and sheet rows."""

import json
import sys


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
    """The model's answer as the brief and the rows to append. A pick whose id is not a
    real candidate is dropped rather than trusted."""
    by_id = {c["id"]: c for c in candidates}
    postings_rows = []
    for pick in result.get("picks", []):
        cand = by_id.get(pick["id"])
        if cand is None:
            print(f"model invented id {pick['id']}, dropping", file=sys.stderr)
            continue
        postings_rows.append([
            today, cand["company"], cand["title"], cand["location"], cand["url"],
            pick.get("fit", ""), pick.get("reason", ""), pick.get("risk", ""), "", "",
        ])
    seen_rows = [[today, c["id"], c["url"]] for c in candidates]
    return result["brief_markdown"], postings_rows, seen_rows
