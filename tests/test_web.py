import io
import re
import urllib.parse
from datetime import datetime

import pytest

from jobbrief import llm, mail, profile, registry, sheet
from jobbrief.sheet import ANSWERS_HEADER, RUNS_HEADER
from jobbrief.web import app as web, oauth
from jobbrief.web.app import create_app


ENV = {
    "GCP_OAUTH_CLIENT_ID": "client-id.apps.googleusercontent.com",
    "GCP_OAUTH_CLIENT_SECRET": "client-secret",
    "SESSION_KEY": "session-key",
    "SERVICE_ACCOUNT_JSON": '{"client_email": "bot@example.iam.gserviceaccount.com"}',
    "REGISTRY_SHEET_ID": "registry-sheet-id",
    "OPERATOR_EMAIL": "operator@example.com",
    "GEMINI_API_KEY": "gemini-key",
}

# Shaped like Google's documented responses, hand-built: no recording, no network.
TOKEN_RESPONSE = {"access_token": "access-token", "expires_in": 3599, "token_type": "Bearer",
                  "scope": "openid https://www.googleapis.com/auth/userinfo.email", "id_token": "header.payload.signature"}

USER = "someone@example.com"

# One filled-in form, as the browser posts it.
FORM = {
    "field": "municipal water systems",
    "experience": "6 years, most recently water quality analyst",
    "tools": "GIS - job",
    "search_titles": "water quality analyst",
    "exclude_titles": "none",
    "work_types": "hands-on, field, or outdoor work\nanalyzing data or information",
    "terms-0": "fine",
    "terms-1": "no",
    "physical": ["long drives"],
    "physical_other": "confined spaces",
    "per_listing": ["why it fits you", "salary when listed"],
    "about_you": "Happiest with a sampling kit in the truck.",
}

SETTINGS = {"title_filter": "water quality analyst", "title_exclude": "sales",
            "lookback_days": 3, "max_picks": 10}

# A registry whose second row is this user, so their row on the grid is 3.
REGISTERED = [("first@example.com", "sheet-first", "yes", "daily", "2026-01-01"),
              (USER, "their-sheet-id", "yes", "weekly", "2026-01-02")]
STORED_SETTINGS = [{"key": key, "value": str(value)} for key, value in SETTINGS.items()]
RUNS = [dict(zip(RUNS_HEADER, [f"2026-09-{day:02d}", "40", "3", "0", "sent", "yes",
                               "flash", "1200", "greenhouse:3", ""])) for day in range(1, 13)]
SAVE = {"profile": "New profile prose.", "title_filter": "water quality analyst",
        "title_exclude": "sales", "suggested_titles": "hydrologist", "suggested_excludes": "",
        "suggested_vocabulary": "WDM", "max_picks": "10", "frequency": "daily"}
PREVIOUS = dict({column: "" for column in ANSWERS_HEADER},
                field="municipal water systems", experience="6 years", terms="on-call: no",
                physical="long drives", per_listing="why it fits you", submitted="2026-09-01")


def fake_oauth(email, verified=True):
    def request(url, data=None, token=None):
        if url == oauth.TOKEN:
            assert data["grant_type"] == "authorization_code"
            return dict(TOKEN_RESPONSE)
        assert url == oauth.USERINFO and token == TOKEN_RESPONSE["access_token"]
        return {"sub": "100000000000000000001", "email": email, "email_verified": verified, "name": "Someone"}
    return request


def fake_tabs(monkeypatch, allowed=(), users=(), sheet_id="their-sheet-id", **user_sheet):
    """Stand in for every tab read, keyed by the sheet it belongs to: the registry's two
    tabs from `allowed` and `users`, and whatever the user's own sheet holds."""
    tabs = {(ENV["REGISTRY_SHEET_ID"], "Allowed"): [{"email": address, "added": "2026-01-01"} for address in allowed],
            (ENV["REGISTRY_SHEET_ID"], "Users"): [dict(zip(registry.USERS_HEADER, row)) for row in users]}
    tabs.update({(sheet_id, tab): rows for tab, rows in user_sheet.items()})
    monkeypatch.setattr(sheet, "read_tab", lambda token, target, tab: tabs.get((target, tab), []))


def value_writes(calls):
    """Every write to a range, in order, as (range, rows). A header row and the row under it
    are separate writes and stay that way."""
    return [(urllib.parse.unquote(url.split("/values/")[1].split("?")[0]), body["values"])
            for method, url, token, body in calls if "/values/" in url]


def written_to(writes, target):
    """The rows written to one range, and a failure if anything wrote there twice."""
    rows = [values for where, values in writes if where == target]
    assert len(rows) == 1, f"{len(rows)} writes to {target}"
    return rows[0]


def fake_sheets(monkeypatch, permissions=()):
    """Record every Sheets and Drive request, on a registry whose `Users` tab is sheet 7."""
    calls = []

    def api(method, url, token, body=None):
        calls.append((method, url, token, body))
        if method == "POST" and url.endswith("/spreadsheets"):
            return {"spreadsheetId": "new-sheet-id"}
        if method == "GET" and "/permissions" in url:
            return {"permissions": list(permissions)}
        if method == "GET" and "fields=sheets" in url:
            return {"sheets": [{"properties": {"sheetId": 7, "title": "Users"}}]}
        return {}

    monkeypatch.setattr(sheet, "api", api)
    return calls


@pytest.fixture
def client(monkeypatch):
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(web, "_allowed", (0.0, frozenset()))
    monkeypatch.setattr(web, "_stash", {})
    monkeypatch.setattr(sheet, "service_account_token", lambda key_json: "sa-token")
    return create_app().test_client()


@pytest.fixture
def sent(monkeypatch):
    messages = []
    monkeypatch.setattr(mail, "send", lambda to, subject, html, text: messages.append((to, subject, text)))
    return messages


def sign_in(client, monkeypatch, email=USER, allowed=(USER,), users=(), verified=True, **user_sheet):
    """Drive the sign-in callback as Google would."""
    fake_tabs(monkeypatch, allowed=allowed, users=users, **user_sheet)
    monkeypatch.setattr(oauth, "request", fake_oauth(email, verified))
    with client.session_transaction() as session:
        session["state"] = "state-token"
    return client.get("/auth/callback?code=auth-code&state=state-token")


def submit(client, monkeypatch, drafted, resume=None):
    """Post the form, with `drafted` standing in for the model stage."""
    monkeypatch.setattr(profile, "draft_profile", drafted)
    data = dict(FORM, resume=(io.BytesIO(resume[1]), resume[0])) if resume else dict(FORM)
    return client.post("/signup", data=data, content_type="multipart/form-data")


def drafts(profile_text="## Summary\n\nA water person.", settings=SETTINGS):
    """A model stage that records its arguments and returns one profile."""
    calls = []

    def draft_profile(answers, models, api_key, retries, resume=None):
        calls.append({"answers": answers, "resume": resume})
        return profile_text, dict(settings)

    return draft_profile, calls


def test_login_asks_for_identity_scopes_only(client):
    target = client.get("/auth/login").headers["Location"]
    assert "scope=openid+email+profile" in target
    assert "drive" not in target


def test_an_address_on_the_allowed_tab_signs_in_whatever_its_case(client, monkeypatch, sent):
    response = sign_in(client, monkeypatch, USER, allowed=["Someone@Example.com "])
    assert response.headers["Location"] == "/"
    assert client.get("/").headers["Location"] == "/signup"
    assert sent == []


def test_an_unknown_address_gets_the_invite_page_and_one_email_naming_it(client, monkeypatch, sent):
    response = sign_in(client, monkeypatch, "stranger@example.com")
    assert response.headers["Location"] == "/invite"
    assert b"Invite only" in client.get("/invite").data
    assert len(sent) == 1
    to, _, text = sent[0]
    assert to == "operator@example.com" and "stranger@example.com" in text
    assert ENV["REGISTRY_SHEET_ID"] in text  # the row goes in from the email
    with client.session_transaction() as session:
        assert "email" not in session


def test_an_unverified_address_is_refused(client, monkeypatch, sent):
    assert sign_in(client, monkeypatch, verified=False).status_code == 400
    assert sent == []


def test_a_mismatched_state_is_refused(client):
    with client.session_transaction() as session:
        session["state"] = "state-token"
    assert client.get("/auth/callback?code=auth-code&state=other").status_code == 400


def test_pages_behind_the_gate_send_a_stranger_to_sign_in(client):
    assert client.get("/").headers["Location"] == "/signin"
    assert client.get("/signup").headers["Location"] == "/signin"
    assert b"Sign in with Google" in client.get("/signin").data




def test_submit_drafts_the_profile_and_asks_for_drive_consent(client, monkeypatch):
    sign_in(client, monkeypatch)
    draft_profile, drafted = drafts()
    response = submit(client, monkeypatch, draft_profile, resume=("resume.pdf", b"%PDF-1.4"))

    target = response.headers["Location"]
    assert "scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fdrive.file" in target
    assert f"login_hint={USER.replace('@', '%40')}" in target
    assert drafted[0]["resume"] == ("resume.pdf", b"%PDF-1.4")
    answers = drafted[0]["answers"]
    assert answers["field"] == FORM["field"]
    assert answers["resume"] == "resume.pdf"
    assert answers["terms"] == "weekends or evenings: fine\non-call: no"
    assert answers["physical"] == "long drives\nconfined spaces"
    assert answers["per_listing"] == "why it fits you\nsalary when listed"
    # The stash holds the answers between the two requests; the resume bytes are gone.
    held = next(iter(web._stash.values()))
    assert "%PDF" not in repr(held)


def test_the_drive_callback_creates_the_sheet_registers_the_user_and_welcomes_them(client, monkeypatch, sent):
    sign_in(client, monkeypatch)
    draft_profile, _ = drafts()
    submit(client, monkeypatch, draft_profile)
    calls = fake_sheets(monkeypatch)
    with client.session_transaction() as session:
        state = session["state"]

    response = client.get(f"/auth/drive/callback?code=drive-code&state={state}")
    assert response.headers["Location"] == "/settings"

    # The sheet is made with the user's own token; only the registry is touched as the bot.
    created = next(call for call in calls if call[0] == "POST" and call[1].endswith("/spreadsheets"))
    assert created[2] == "access-token"
    share = next((url, token, body) for method, url, token, body in calls if "/permissions" in url)
    assert share[1] == "access-token" and "sendNotificationEmail=false" in share[0]
    assert share[2] == {"type": "user", "role": "writer", "emailAddress": "bot@example.iam.gserviceaccount.com"}

    written = value_writes(calls)
    row = dict(zip(ANSWERS_HEADER, written_to(written, "Answers!A2")[0]))
    assert row["field"] == FORM["field"] and row["resume"] == ""
    assert row["submitted"] == datetime.now().strftime("%Y-%m-%d")
    assert written_to(written, "Profile!A1") == [["## Summary\n\nA water person."]]
    assert written_to(written, "Settings!A2") == [[key, value] for key, value in SETTINGS.items()]

    roster = next((token, body) for method, url, token, body in calls if "Users:append" in url)
    assert roster[0] == "sa-token"
    assert dict(zip(registry.USERS_HEADER, roster[1]["values"][0])) == {
        "email": USER, "sheet_id": "new-sheet-id", "active": "yes", "frequency": "daily",
        "added": datetime.now().strftime("%Y-%m-%d")}

    to, subject, text = sent[0]
    assert to == USER and "new-sheet-id" in text and "/settings" in text


def test_a_lost_stash_sends_the_user_back_to_the_form_with_nothing_created(client, monkeypatch, sent):
    sign_in(client, monkeypatch)
    draft_profile, _ = drafts()
    submit(client, monkeypatch, draft_profile)
    web._stash.clear()
    calls = fake_sheets(monkeypatch)
    with client.session_transaction() as session:
        state = session["state"]

    response = client.get(f"/auth/drive/callback?code=drive-code&state={state}")
    assert b"Filling the form in again" in response.data
    assert calls == [] and sent == []


def test_a_model_failure_ends_signup_before_anything_is_created(client, monkeypatch, sent):
    sign_in(client, monkeypatch)

    def refuse(*args, **kwargs):
        raise llm.ModelError("all 8 attempts failed")

    response = submit(client, monkeypatch, refuse)
    assert response.status_code == 200
    assert b"could not draft a profile" in response.data
    assert FORM["field"].encode() in response.data  # the answers are still in the form
    assert web._stash == {} and sent == []


def registered(client, monkeypatch, **user_sheet):
    """Sign in as a user who already has a sheet."""
    sign_in(client, monkeypatch, users=REGISTERED, Settings=STORED_SETTINGS, **user_sheet)


def test_settings_shows_the_profile_the_filters_the_frequency_and_the_last_ten_runs(client, monkeypatch):
    registered(client, monkeypatch, Runs=RUNS)
    monkeypatch.setattr(sheet, "read_cell", lambda token, sheet_id, cell: "## Summary\n\nA water person.")
    page = client.get("/settings").data.decode()
    assert "their-sheet-id" in page and "A water person." in page
    assert "water quality analyst" in page
    assert re.search(r'value="weekly"\s+checked', page)
    assert not re.search(r'name="paused"\s+checked', page)
    assert page.count("greenhouse:3") == 10
    assert "2026-09-12" in page and "2026-09-02" not in page  # newest first, the oldest two dropped


def test_saving_writes_the_profile_the_settings_and_the_registry_row(client, monkeypatch):
    registered(client, monkeypatch)
    calls = fake_sheets(monkeypatch)
    response = client.post("/settings", data=dict(SAVE, title_filter="hydrologist", max_picks="5"))
    assert response.headers["Location"] == "/settings"
    written = value_writes(calls)
    assert written_to(written, "Profile!A1") == [["New profile prose."]]
    # lookback_days is not on the page, and the save leaves it as it was.
    assert written_to(written, "Settings!A2") == [["title_filter", "hydrologist"], ["title_exclude", "sales"],
                                                  ["lookback_days", "3"], ["max_picks", "5"],
                                                  ["suggested_titles", "hydrologist"], ["suggested_excludes", ""],
                                                  ["suggested_vocabulary", "WDM"]]
    # Row 3 of the registry: the second user under the header.
    assert written_to(written, "Users!A3") == [[USER, "their-sheet-id", "yes", "daily", "2026-01-02"]]


def test_pausing_and_unpausing_flip_active_on_the_registry_row(client, monkeypatch):
    registered(client, monkeypatch)
    calls = fake_sheets(monkeypatch)
    active = registry.USERS_HEADER.index("active")

    client.post("/settings", data=dict(SAVE, paused="on"))
    assert written_to(value_writes(calls), "Users!A3")[0][active] == "no"
    calls.clear()
    client.post("/settings", data=SAVE)
    written = value_writes(calls)
    assert written_to(written, "Users!A3")[0][active] == "yes"
    assert written_to(written, "Profile!A1") == [["New profile prose."]]  # unpausing is not a wipe


def test_retake_prefills_the_form_and_rewrites_the_answers_without_touching_drive(client, monkeypatch):
    registered(client, monkeypatch, Answers=[PREVIOUS])
    page = client.get("/signup").data.decode()
    assert "Retake the questionnaire" in page
    assert 'value="municipal water systems"' in page
    assert re.search(r'value="long drives"\s+checked', page)
    assert re.search(r'name="terms-1" value="no"\s+checked', page)  # on-call, as it was answered

    redrawn = dict(SETTINGS, title_filter="hydrologist", vocabulary="GIS", suggested_titles="hydrologist II",
                   suggested_excludes="intern", suggested_vocabulary="WDM", lookback_days=9, max_picks=99)
    draft_profile, drafted = drafts("## Summary\n\nA redrafted person.", settings=redrawn)
    calls = fake_sheets(monkeypatch)
    response = submit(client, monkeypatch, draft_profile)
    assert response.headers["Location"] == "/settings"
    assert drafted[0]["answers"]["field"] == FORM["field"]
    written = value_writes(calls)
    assert written_to(written, "Answers!A2")[0][0] == FORM["field"]
    assert written_to(written, "Profile!A1") == [["## Summary\n\nA redrafted person."]]
    # The filters, vocabulary, and suggestions are redrawn; the pick cap and lookback are the user's, not the model's.
    assert written_to(written, "Settings!A2") == [["title_filter", "hydrologist"], ["title_exclude", "sales"],
                                                  ["lookback_days", "3"], ["max_picks", "10"], ["vocabulary", "GIS"],
                                                  ["suggested_titles", "hydrologist II"], ["suggested_excludes", "intern"],
                                                  ["suggested_vocabulary", "WDM"]]
    # No second sheet, no second consent, and the run's history is untouched.
    assert not any(url.endswith("/spreadsheets") or "/permissions" in url for _, url, _, _ in calls)
    assert {where for where, _ in written} == {"Answers!A2", "Profile!A1", "Settings!A2"}
    assert web._stash == {}


def test_delete_me_removes_the_registry_row_and_the_service_accounts_access(client, monkeypatch):
    registered(client, monkeypatch)
    assert b"Remove me" in client.get("/delete").data
    editor = [{"id": "p1", "role": "writer", "emailAddress": "bot@example.iam.gserviceaccount.com"}]
    calls = fake_sheets(monkeypatch, permissions=editor)
    dropped = []
    monkeypatch.setattr(sheet, "delete_row",
                        lambda token, target, tab, number: dropped.append((target, tab, number)))

    assert b"Removed" in client.post("/delete").data
    assert dropped == [(ENV["REGISTRY_SHEET_ID"], "Users", 3)]
    assert any(method == "DELETE" and url.endswith("/permissions/p1") for method, url, _, _ in calls)
    assert client.get("/settings").headers["Location"] == "/signin"


def test_a_registered_user_lands_on_settings(client, monkeypatch):
    registered(client, monkeypatch)
    assert client.get("/").headers["Location"] == "/settings"


@pytest.mark.parametrize("method", ["get", "post"])
def test_settings_without_a_registry_row_sends_the_user_to_the_form(client, monkeypatch, method):
    sign_in(client, monkeypatch)
    assert getattr(client, method)("/settings").headers["Location"] == "/signup"


def test_delete_without_a_registry_row_still_ends_the_session(client, monkeypatch):
    sign_in(client, monkeypatch)
    calls = fake_sheets(monkeypatch)
    assert b"Removed" in client.post("/delete").data
    assert calls == []
    assert client.get("/settings").headers["Location"] == "/signin"


def test_the_drive_callback_refuses_a_mismatched_state(client, monkeypatch):
    sign_in(client, monkeypatch)
    draft_profile, _ = drafts()
    submit(client, monkeypatch, draft_profile)
    calls = fake_sheets(monkeypatch)
    assert client.get("/auth/drive/callback?code=drive-code&state=other").status_code == 400
    assert calls == []


def test_signing_out_ends_the_session(client, monkeypatch):
    sign_in(client, monkeypatch)
    assert client.post("/signout").headers["Location"] == "/signin"
    assert client.get("/").headers["Location"] == "/signin"


def test_a_session_whose_allowed_row_is_gone_is_sent_to_the_invite_page(client, monkeypatch):
    registered(client, monkeypatch)
    fake_tabs(monkeypatch, allowed=[], users=REGISTERED, Settings=STORED_SETTINGS)
    monkeypatch.setattr(web, "_allowed", (0.0, frozenset()))
    assert client.get("/settings").headers["Location"] == "/invite"
    assert client.post("/signout").headers["Location"] == "/signin"
