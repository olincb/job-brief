from jobbrief import sheet
from jobbrief.sheet import (ANSWERS_HEADER, POSTINGS_HEADER, RUNS_HEADER, SEEN_HEADER,
                            SETTINGS_HEADER, TABS)


def fake_api(monkeypatch, sheet_id, existing_tabs, permissions=()):
    """Stand in for the one request function on a sheet with the given tabs and permissions.
    Records every call so a test can assert what init_sheet would change."""
    calls = []

    def api(method, url, token, body=None):
        calls.append((method, url, body))
        if method == "POST" and url.endswith("/spreadsheets"):
            return {"spreadsheetId": sheet_id}
        if method == "GET" and "/permissions" in url:
            return {"permissions": list(permissions)}
        if method == "GET" and "fields=sheets" in url:
            return {"sheets": [{"properties": {"sheetId": index, "title": title}}
                               for index, title in enumerate(existing_tabs)]}
        return {}

    monkeypatch.setattr(sheet, "api", api)
    return calls


def headers_written(calls):
    """The header rows init_sheet wrote, in order."""
    return [body["values"][0] for method, _, body in calls if method == "PUT"]


# All six but Profile, whose A1 is the user's prose and never ours to write.
HEADED = [ANSWERS_HEADER, SETTINGS_HEADER, POSTINGS_HEADER, SEEN_HEADER, RUNS_HEADER]


def test_init_sheet_creates_all_six_tabs_and_writes_every_header_but_profile(monkeypatch, capsys):
    calls = fake_api(monkeypatch, "new-sheet-id", existing_tabs=[])
    sheet_id = sheet.init_sheet("token", "", "Job Brief")
    assert sheet_id == "new-sheet-id"
    assert "created spreadsheet new-sheet-id" in capsys.readouterr().out
    create = next(body for method, url, body in calls if method == "POST" and url.endswith("/spreadsheets"))
    assert create["properties"]["title"] == "Job Brief"
    assert [tab["properties"]["title"] for tab in create["sheets"]] == list(TABS)
    assert headers_written(calls) == HEADED
    assert not any(method == "PUT" and "Profile" in url for method, url, _ in calls)


def test_init_sheet_adds_missing_tabs_and_leaves_profile_alone(monkeypatch):
    # A pre-#7 sheet: only the old three tabs. Answers, Profile, Settings are added.
    calls = fake_api(monkeypatch, "unused", existing_tabs=["Postings", "Seen", "Runs"])
    sheet.init_sheet("token", "existing-id", "Job Brief")
    assert not any(url.endswith("/spreadsheets") for _, url, _ in calls)  # no create
    added = [body["requests"][0]["addSheet"]["properties"]["title"] for _, url, body in calls if "batchUpdate" in url]
    assert added == ["Answers", "Profile", "Settings"]
    assert headers_written(calls) == HEADED
    # Profile's A1 is the user's profile prose: init adds the tab but never writes it.
    assert not any(method == "PUT" and "Profile" in url for method, url, _ in calls)


def test_read_tab_keys_rows_by_header_and_pads_short_rows(monkeypatch):
    grid = {"values": [SEEN_HEADER, ["2026-09-01", "greenhouse:acme:1"]]}  # url column absent
    monkeypatch.setattr(sheet, "api", lambda *args, **kwargs: grid)
    assert sheet.read_tab("token", "sheet-id", "Seen") == [
        {"date_seen": "2026-09-01", "id": "greenhouse:acme:1", "url": ""},
    ]


def test_add_editor_grants_edit_access_and_can_skip_the_notification(monkeypatch):
    calls = fake_api(monkeypatch, "unused", existing_tabs=[])
    sheet.add_editor("token", "sheet-id", "bot@example.iam.gserviceaccount.com", notify=False)
    url, body = next((url, body) for method, url, body in calls if method == "POST")
    assert "sendNotificationEmail=false" in url
    assert body == {"type": "user", "role": "writer", "emailAddress": "bot@example.iam.gserviceaccount.com"}


def test_remove_editor_drops_the_account_whatever_case_drive_reports_it_in(monkeypatch):
    editor = [{"id": "p1", "role": "writer", "emailAddress": "Bot@Example.iam.gserviceaccount.com"}]
    calls = fake_api(monkeypatch, "unused", existing_tabs=[], permissions=editor)
    sheet.remove_editor("token", "sheet-id", "bot@example.iam.gserviceaccount.com")
    assert [url for method, url, _ in calls if method == "DELETE"] == [f"{sheet.DRIVE}/sheet-id/permissions/p1"]


def test_delete_row_finds_the_tab_by_name_and_removes_that_one_row(monkeypatch):
    calls = fake_api(monkeypatch, "unused", existing_tabs=["Allowed", "Users"])
    sheet.delete_row("token", "registry-id", "Users", 3)
    body = next(body for _, url, body in calls if "batchUpdate" in url)
    assert body["requests"][0]["deleteDimension"]["range"] == {
        "sheetId": 1, "dimension": "ROWS", "startIndex": 2, "endIndex": 3}


def test_read_cell_returns_the_text_and_an_empty_cell_as_an_empty_string(monkeypatch):
    monkeypatch.setattr(sheet, "api", lambda *args, **kwargs: {"values": [["the profile prose"]]})
    assert sheet.read_cell("token", "sheet-id", "Profile!A1") == "the profile prose"
    monkeypatch.setattr(sheet, "api", lambda *args, **kwargs: {})
    assert sheet.read_cell("token", "sheet-id", "Profile!A1") == ""
