"""The tracking sheet: tab layout, the heartbeat rule over Runs rows, and a small client
for the Sheets and Drive REST APIs. Every request is authenticated with a bearer token so
the same functions serve the service account (daily job) and a user's short-lived
drive.file token (signup)."""

import json
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from google.auth import crypt, jwt


# Sheet layout. Postings holds the picks and is edited by hand (status, notes).
# Seen holds every candidate ever shown to the model so it is never re-scored.
POSTINGS_HEADER = ["date_seen", "company", "title", "location", "url", "fit", "reason", "risk", "status", "notes"]
SEEN_HEADER = ["date_seen", "id", "url"]
# Runs is the health log: one row per run. The heartbeat reads it to decide whether
# enough quiet days have passed to say "still here".
RUNS_HEADER = ["date", "candidates", "picks", "skipped_sources", "outcome", "emailed", "model", "tokens"]

TABS = {"Postings": POSTINGS_HEADER, "Seen": SEEN_HEADER, "Runs": RUNS_HEADER}

SHEETS = "https://sheets.googleapis.com/v4/spreadsheets"
DRIVE = "https://www.googleapis.com/drive/v3/files"


def _rows_by_header(values):
    """A tab's raw value grid -> list of dicts keyed by the header row, short rows padded."""
    if len(values) < 2:
        return []
    header, rows = values[0], values[1:]
    return [dict(zip(header, row + [""] * (len(header) - len(row)))) for row in rows]


def load_sheet_rows(path):
    """A saved `values.get` dump on disk -> rows keyed by the header. The staged CLI reads
    tab dumps from files; the live equivalent is read_tab."""
    if not Path(path).exists():
        return []
    return _rows_by_header(json.loads(Path(path).read_text()).get("values", []))


def days_since_email(runs_rows, today):
    """Days since the last run that emailed, according to the Runs tab, or None when no
    email is on record."""
    emailed = [row["date"] for row in runs_rows if row.get("emailed") == "yes" and row.get("date")]
    last = max((datetime.fromisoformat(d).date() for d in emailed), default=None)
    return (today - last).days if last else None


def service_account_token(key_json):
    """Mint a short-lived bearer token from a service-account key. google-auth signs the
    RS256 JWT because the standard library has no RSA; the exchange is a plain POST."""
    info = json.loads(key_json)
    now = int(time.time())
    assertion = jwt.encode(crypt.RSASigner.from_service_account_info(info), {
        "iss": info["client_email"],
        "scope": "https://www.googleapis.com/auth/spreadsheets https://www.googleapis.com/auth/drive.file",
        "aud": "https://oauth2.googleapis.com/token",
        "iat": now,
        "exp": now + 3600,
    }).decode()
    data = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": assertion,
    }).encode()
    with urllib.request.urlopen("https://oauth2.googleapis.com/token", data=data) as response:
        return json.load(response)["access_token"]


def api(method, url, token, body=None):
    """One authenticated JSON request; the whole network surface of this module goes
    through here. HTTPError propagates."""
    payload = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=payload, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(request) as response:
        text = response.read()
    return json.loads(text) if text else {}


def read_tab(token, spreadsheet_id, tab):
    """Read one tab into rows keyed by its header, the live form of load_sheet_rows."""
    values = api("GET", f"{SHEETS}/{spreadsheet_id}/values/{urllib.parse.quote(tab)}", token).get("values", [])
    return _rows_by_header(values)


def append_rows(token, spreadsheet_id, tab, rows):
    """Append rows below whatever the tab already holds."""
    url = f"{SHEETS}/{spreadsheet_id}/values/{urllib.parse.quote(tab)}:append?valueInputOption=RAW"
    api("POST", url, token, {"values": rows})


def write_range(token, spreadsheet_id, cell_range, rows):
    """Overwrite a range with rows, anchored at cell_range (e.g. "Postings!A1")."""
    url = f"{SHEETS}/{spreadsheet_id}/values/{urllib.parse.quote(cell_range)}?valueInputOption=RAW"
    api("PUT", url, token, {"values": rows})


def create_spreadsheet(token, title, tabs):
    """Create a spreadsheet with the named tabs and return its id."""
    body = {"properties": {"title": title}, "sheets": [{"properties": {"title": tab}} for tab in tabs]}
    return api("POST", SHEETS, token, body)["spreadsheetId"]


def _find_permission(token, spreadsheet_id, email):
    listing = api("GET", f"{DRIVE}/{spreadsheet_id}/permissions?fields=permissions(id,role,emailAddress)", token)
    for perm in listing.get("permissions", []):
        if (perm.get("emailAddress") or "").lower() == email.lower():
            return perm
    return None


def add_editor(token, spreadsheet_id, email):
    """Grant one Google account edit access, sending Drive's share notification."""
    url = f"{DRIVE}/{spreadsheet_id}/permissions?sendNotificationEmail=true"
    api("POST", url, token, {"type": "user", "role": "writer", "emailAddress": email})


def remove_editor(token, spreadsheet_id, email):
    """Drop one account's access. The delete-me and operator-removal paths use this."""
    perm = _find_permission(token, spreadsheet_id, email)
    if perm:
        api("DELETE", f"{DRIVE}/{spreadsheet_id}/permissions/{perm['id']}", token)


def init_sheet(token, sheet_id, title):
    """Create the spreadsheet when no id is given. Either way, add whichever of the three
    tabs are missing and write every header row. Row 1 is ours alone, so rewriting it is
    safe; this is how a new column reaches an existing sheet, and older rows simply have a
    blank in it. Returns the sheet id."""
    if not sheet_id:
        sheet_id = create_spreadsheet(token, title, TABS)
        print(f"created spreadsheet {sheet_id}")
        existing = set(TABS)
    else:
        meta = api("GET", f"{SHEETS}/{sheet_id}?fields=sheets.properties.title", token)
        existing = {sheet["properties"]["title"] for sheet in meta.get("sheets", [])}
    for tab in TABS:
        if tab not in existing:
            api("POST", f"{SHEETS}/{sheet_id}:batchUpdate", token,
                {"requests": [{"addSheet": {"properties": {"title": tab}}}]})
            print(f"added tab {tab}")
    for tab, header in TABS.items():
        write_range(token, sheet_id, f"{tab}!A1", [header])
        print(f"wrote header row for {tab}")
    return sheet_id


def share_sheet(token, sheet_id, email):
    """Give one Google account edit access, once. The sheet is owned by the token holder,
    so the person whose brief it is needs this to set status and notes. Requires the
    drive.file scope, which covers files this token created."""
    perm = _find_permission(token, sheet_id, email)
    if perm and perm.get("role") in ("writer", "owner"):
        return
    add_editor(token, sheet_id, email)
    print(f"shared {sheet_id} as editor")
