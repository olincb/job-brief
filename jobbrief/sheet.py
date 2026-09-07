"""The tracking sheet: tab layout, reading a gws dump of a tab, the heartbeat rule over
Runs rows, and creating or upgrading a sheet through the gws CLI."""

import json
import subprocess
from datetime import datetime
from pathlib import Path


# Sheet layout. Postings holds the picks and is edited by hand (status, notes).
# Seen holds every candidate ever shown to the model so it is never re-scored.
POSTINGS_HEADER = ["date_seen", "company", "title", "location", "url", "fit", "reason", "risk", "status", "notes"]
SEEN_HEADER = ["date_seen", "id", "url"]
# Runs is the health log: one row per run. The heartbeat reads it to decide whether
# enough quiet days have passed to say "still here".
RUNS_HEADER = ["date", "candidates", "picks", "skipped_sources", "outcome", "emailed", "model", "tokens"]


def load_sheet_rows(path):
    """A `gws sheets spreadsheets values get` dump -> list of dicts keyed by the header row."""
    if not Path(path).exists():
        return []
    values = json.loads(Path(path).read_text()).get("values", [])
    if len(values) < 2:
        return []
    header, rows = values[0], values[1:]
    return [dict(zip(header, row + [""] * (len(header) - len(row)))) for row in rows]


def days_since_email(runs_rows, today):
    """Days since the last run that emailed, according to the Runs tab, or None when no
    email is on record."""
    emailed = [row["date"] for row in runs_rows if row.get("emailed") == "yes" and row.get("date")]
    last = max((datetime.fromisoformat(d).date() for d in emailed), default=None)
    return (today - last).days if last else None


TABS = {"Postings": POSTINGS_HEADER, "Seen": SEEN_HEADER, "Runs": RUNS_HEADER}


def gws(*args):
    """Run a gws command and parse its JSON output."""
    result = subprocess.run(["gws", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"gws {' '.join(args[:3])} failed:\n{result.stderr.strip()}")
    return json.loads(result.stdout) if result.stdout.strip() else {}


def create_sheet(title):
    created = gws("sheets", "spreadsheets", "create", "--json", json.dumps({
        "properties": {"title": title},
        "sheets": [{"properties": {"title": tab}} for tab in TABS],
    }))
    return created["spreadsheetId"]


def ensure_layout(sheet_id):
    """Add whichever of the three tabs are missing and write any header row that does not
    match the current columns. Row 1 is ours alone, so rewriting it is safe; this is how a
    new column reaches an existing sheet, and older rows simply have a blank in it."""
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
        gws("sheets", "spreadsheets", "values", "update",
            "--params", json.dumps({"spreadsheetId": sheet_id, "range": f"{tab}!A1", "valueInputOption": "RAW"}),
            "--json", json.dumps({"values": [header]}))
        print(f"wrote header row for {tab}" if not first_row[0] else f"updated header row for {tab}")


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
