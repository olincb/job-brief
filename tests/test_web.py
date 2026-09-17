import io

import pytest

from jobbrief import mail, profile, registry, sheet
from jobbrief.sheet import ANSWERS_HEADER
from jobbrief.web import app as web, oauth
from jobbrief.web.app import create_app


ENV = {
    "GOOGLE_CLIENT_ID": "client-id.apps.googleusercontent.com",
    "GOOGLE_CLIENT_SECRET": "client-secret",
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


def fake_oauth(email, verified=True):
    def request(url, data=None, token=None):
        if url == oauth.TOKEN:
            assert data["grant_type"] == "authorization_code"
            return dict(TOKEN_RESPONSE)
        assert url == oauth.USERINFO and token == TOKEN_RESPONSE["access_token"]
        return {"sub": "100000000000000000001", "email": email, "email_verified": verified, "name": "Someone"}
    return request


def fake_tabs(monkeypatch, allowed=(), users=()):
    """Stand in for the registry: `allowed` are addresses, `users` are Users rows as tuples."""
    tabs = {"Allowed": [{"email": address, "added": "2026-01-01"} for address in allowed],
            "Users": [dict(zip(registry.USERS_HEADER, row)) for row in users]}
    monkeypatch.setattr(sheet, "read_tab", lambda token, sheet_id, tab: tabs[tab])


def value_writes(calls):
    """The last write to each tab: init_sheet lays the header row, then signup writes over
    it or appends beneath it."""
    return {url.split("/values/")[1].split("?")[0].split("%21")[0].split(":")[0]: body["values"]
            for method, url, token, body in calls if "/values/" in url}


def fake_sheets(monkeypatch):
    """Record every Sheets and Drive request signup would make."""
    calls = []

    def api(method, url, token, body=None):
        calls.append((method, url, token, body))
        return {"spreadsheetId": "new-sheet-id"} if method == "POST" and url.endswith("/spreadsheets") else {}

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


def sign_in(client, monkeypatch, email=USER, allowed=(USER,), users=(), verified=True):
    """Drive the sign-in callback as Google would."""
    fake_tabs(monkeypatch, allowed=allowed, users=users)
    monkeypatch.setattr(oauth, "request", fake_oauth(email, verified))
    with client.session_transaction() as session:
        session["state"] = "state-token"
    return client.get("/auth/callback?code=auth-code&state=state-token")


def submit(client, monkeypatch, drafted, resume=None):
    """Post the form, with `drafted` standing in for the model stage."""
    monkeypatch.setattr(profile, "draft_profile", drafted)
    data = dict(FORM, resume=(io.BytesIO(resume[1]), resume[0])) if resume else dict(FORM)
    return client.post("/signup", data=data, content_type="multipart/form-data")


def drafts(profile_text="## Summary\n\nA water person."):
    """A model stage that records its arguments and returns one profile."""
    calls = []

    def draft_profile(answers, models, api_key, retries, resume=None):
        calls.append({"answers": answers, "resume": resume})
        return profile_text, dict(SETTINGS)

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


def test_the_operator_is_allowed_without_a_row(client, monkeypatch):
    assert sign_in(client, monkeypatch, "operator@example.com", allowed=[]).headers["Location"] == "/"


def test_an_unknown_address_gets_the_invite_page_and_one_email_naming_it(client, monkeypatch, sent):
    response = sign_in(client, monkeypatch, "stranger@example.com")
    assert response.headers["Location"] == "/invite"
    assert b"Invite only" in client.get("/invite").data
    assert len(sent) == 1
    to, _, text = sent[0]
    assert to == "operator@example.com" and "stranger@example.com" in text
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


def test_a_registered_user_gets_settings_rather_than_the_form(client, monkeypatch):
    sign_in(client, monkeypatch, users=[(USER, "their-sheet-id", "yes", "daily", "2026-01-01")])
    assert client.get("/signup").headers["Location"] == "/settings"
    assert b"their-sheet-id" in client.get("/settings").data


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
    assert set(held) == {"answers", "profile", "settings", "created"}


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
    assert written["Answers"] == [[
        FORM["field"], FORM["experience"], "", FORM["tools"], "", FORM["work_types"],
        FORM["search_titles"], FORM["exclude_titles"], "", "", "",
        "weekends or evenings: fine\non-call: no", "long drives\nconfined spaces", "", "",
        FORM["about_you"], "", "", "why it fits you\nsalary when listed", "",
        written["Answers"][0][ANSWERS_HEADER.index("submitted")],
    ]]
    assert written["Profile"] == [["## Summary\n\nA water person."]]
    assert written["Settings"] == [[key, value] for key, value in SETTINGS.items()]

    registered = next((token, body) for method, url, token, body in calls
                      if "registry-sheet-id" in url and "Users" in url)
    assert registered[0] == "sa-token"
    assert registered[1]["values"] == [[USER, "new-sheet-id", "no", "daily",
                                        registered[1]["values"][0][-1]]]

    to, subject, text = sent[0]
    assert to == USER and "new-sheet-id" in text and "operator" in text.lower()


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
        raise SystemExit("all 8 attempts failed")

    response = submit(client, monkeypatch, refuse)
    assert response.status_code == 200
    assert b"could not draft a profile" in response.data
    assert web._stash == {} and sent == []
