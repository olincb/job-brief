from jobbrief import sheet
from jobbrief.sheet import POSTINGS_HEADER, RUNS_HEADER, SEEN_HEADER, TABS


def fake_api(monkeypatch, sheet_id, existing_tabs, permissions=()):
    """Stand in for the one request function on a sheet with the given tabs and shares.
    Records every call so a test can assert what init_sheet or share_sheet would change."""
    calls = []

    def api(method, url, token, body=None):
        calls.append((method, url, body))
        if method == "POST" and url.endswith("/spreadsheets"):
            return {"spreadsheetId": sheet_id}
        if method == "GET" and "/permissions" in url:
            return {"permissions": list(permissions)}
        if method == "GET" and "fields=sheets" in url:
            return {"sheets": [{"properties": {"title": t}} for t in existing_tabs]}
        return {}

    monkeypatch.setattr(sheet, "api", api)
    return calls


def headers_written(calls):
    """The header rows init_sheet wrote, in order."""
    return [body["values"][0] for method, _, body in calls if method == "PUT"]


def test_init_sheet_creates_and_writes_every_header_when_no_id(monkeypatch, capsys):
    calls = fake_api(monkeypatch, "new-sheet-id", existing_tabs=[])
    sheet_id = sheet.init_sheet("token", "", "Job Brief")
    assert sheet_id == "new-sheet-id"
    assert "created spreadsheet new-sheet-id" in capsys.readouterr().out
    create = next(body for method, url, body in calls if method == "POST" and url.endswith("/spreadsheets"))
    assert create["properties"]["title"] == "Job Brief"
    assert [tab["properties"]["title"] for tab in create["sheets"]] == list(TABS)
    assert headers_written(calls) == [POSTINGS_HEADER, SEEN_HEADER, RUNS_HEADER]


def test_init_sheet_adds_missing_tabs_and_upgrades_headers_in_place(monkeypatch):
    calls = fake_api(monkeypatch, "unused", existing_tabs=["Postings"])
    sheet.init_sheet("token", "existing-id", "Job Brief")
    assert not any(url.endswith("/spreadsheets") for _, url, _ in calls)  # no create
    added = [body["requests"][0]["addSheet"]["properties"]["title"] for _, url, body in calls if "batchUpdate" in url]
    assert added == ["Seen", "Runs"]
    assert headers_written(calls) == [POSTINGS_HEADER, SEEN_HEADER, RUNS_HEADER]


def test_read_tab_keys_rows_by_header_and_pads_short_rows(monkeypatch):
    grid = {"values": [SEEN_HEADER, ["2026-09-01", "greenhouse:acme:1"]]}  # url column absent
    monkeypatch.setattr(sheet, "api", lambda *args, **kwargs: grid)
    assert sheet.read_tab("token", "sheet-id", "Seen") == [
        {"date_seen": "2026-09-01", "id": "greenhouse:acme:1", "url": ""},
    ]


def test_share_sheet_grants_edit_access(monkeypatch):
    calls = fake_api(monkeypatch, "unused", existing_tabs=[], permissions=[])
    sheet.share_sheet("token", "sheet-id", "someone@example.com")
    grant = next(body for method, url, body in calls if method == "POST")
    assert grant == {"type": "user", "role": "writer", "emailAddress": "someone@example.com"}


def test_share_sheet_skips_an_account_that_is_already_an_editor(monkeypatch):
    already = [{"id": "p1", "role": "writer", "emailAddress": "someone@example.com"}]
    calls = fake_api(monkeypatch, "unused", existing_tabs=[], permissions=already)
    sheet.share_sheet("token", "sheet-id", "someone@example.com")
    assert not any(method == "POST" for method, _, _ in calls)
