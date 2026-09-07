import argparse
import json

from jobbrief import cli, sheet
from jobbrief.sheet import POSTINGS_HEADER


def fake_gws(monkeypatch, sheet_id, existing_tabs, headers):
    """Stand in for the gws CLI on a sheet with the given tabs and header rows. Records
    every call so a test can assert what init-sheet would have changed."""
    calls = []

    def gws(*args):
        calls.append(args)
        params = json.loads(args[args.index("--params") + 1]) if "--params" in args else {}
        match args[:4]:
            case ("sheets", "spreadsheets", "create", _):
                return {"spreadsheetId": sheet_id}
            case ("sheets", "spreadsheets", "get", _):
                return {"sheets": [{"properties": {"title": t}} for t in existing_tabs]}
            case ("sheets", "spreadsheets", "values", "get"):
                tab = params["range"].split("!")[0]
                return {"values": [headers[tab]]} if tab in headers else {}
            case ("drive", "permissions", "list", _):
                return {"permissions": []}
        return {}

    monkeypatch.setattr(sheet, "gws", gws)
    return calls


def added_tabs(calls):
    return [json.loads(c[c.index("--json") + 1])["requests"][0]["addSheet"]["properties"]["title"] for c in calls if "batchUpdate" in c]


def header_writes(calls):
    return [json.loads(c[c.index("--params") + 1])["range"].split("!")[0] for c in calls if c[:4] == ("sheets", "spreadsheets", "values", "update")]


def test_init_sheet_creates_and_prints_the_id_when_none_is_given(monkeypatch, capsys):
    calls = fake_gws(monkeypatch, "new-sheet-id", ["Postings", "Seen", "Runs"], {})
    cli.cmd_init_sheet(argparse.Namespace(sheet_id="", title="Job Brief", share_with=""))
    out = capsys.readouterr().out
    assert "created spreadsheet new-sheet-id" in out and "spreadsheets/d/new-sheet-id" in out
    assert json.loads(calls[0][calls[0].index("--json") + 1])["properties"]["title"] == "Job Brief"
    assert header_writes(calls) == ["Postings", "Seen", "Runs"]


def test_init_sheet_upgrades_an_existing_sheet_in_place(monkeypatch):
    calls = fake_gws(monkeypatch, "unused", ["Postings"], {"Postings": POSTINGS_HEADER})
    cli.cmd_init_sheet(argparse.Namespace(sheet_id="existing-id", title="Job Brief", share_with=""))
    assert not any("create" in c for c in calls)
    assert added_tabs(calls) == ["Seen", "Runs"]
    assert header_writes(calls) == ["Seen", "Runs"]


def test_init_sheet_shares_with_the_given_account(monkeypatch):
    calls = fake_gws(monkeypatch, "unused", ["Postings", "Seen", "Runs"], {})
    cli.cmd_init_sheet(argparse.Namespace(sheet_id="existing-id", title="Job Brief", share_with="someone@example.com"))
    grant = next(c for c in calls if c[:3] == ("drive", "permissions", "create"))
    assert json.loads(grant[grant.index("--json") + 1]) == {"type": "user", "role": "writer", "emailAddress": "someone@example.com"}
