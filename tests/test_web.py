import pytest

from jobbrief import mail, sheet
from jobbrief.web import app as web, oauth
from jobbrief.web.app import create_app


ENV = {
    "GOOGLE_CLIENT_ID": "client-id.apps.googleusercontent.com",
    "GOOGLE_CLIENT_SECRET": "client-secret",
    "SESSION_KEY": "session-key",
    "SERVICE_ACCOUNT_JSON": "{}",
    "REGISTRY_SHEET_ID": "registry-sheet-id",
    "OPERATOR_EMAIL": "operator@example.com",
}

# Shaped like Google's documented responses, hand-built: no recording, no network.
TOKEN_RESPONSE = {"access_token": "access-token", "expires_in": 3599, "token_type": "Bearer",
                  "scope": "openid https://www.googleapis.com/auth/userinfo.email", "id_token": "header.payload.signature"}


def fake_oauth(email, verified=True):
    def request(url, data=None, token=None):
        if url == oauth.TOKEN:
            assert data["grant_type"] == "authorization_code" and data["code"] == "auth-code"
            return dict(TOKEN_RESPONSE)
        assert url == oauth.USERINFO and token == TOKEN_RESPONSE["access_token"]
        return {"sub": "100000000000000000001", "email": email, "email_verified": verified, "name": "Someone"}
    return request


@pytest.fixture
def client(monkeypatch):
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(web, "_allowed", (0.0, frozenset()))
    monkeypatch.setattr(sheet, "service_account_token", lambda key_json: "sa-token")
    return create_app().test_client()


@pytest.fixture
def sent(monkeypatch):
    messages = []
    monkeypatch.setattr(mail, "send", lambda to, subject, html, text: messages.append((to, subject, text)))
    return messages


def sign_in(client, monkeypatch, email, allowed=(), verified=True):
    """Drive the callback as Google would, with `allowed` standing in for the Allowed tab."""
    monkeypatch.setattr(sheet, "read_tab",
                        lambda token, sheet_id, tab: [{"email": row, "added": "2026-01-01"} for row in allowed])
    monkeypatch.setattr(oauth, "request", fake_oauth(email, verified))
    with client.session_transaction() as session:
        session["state"] = "state-token"
    return client.get("/auth/callback?code=auth-code&state=state-token")


def test_login_asks_for_identity_scopes_only(client):
    target = client.get("/auth/login").headers["Location"]
    assert "scope=openid+email+profile" in target
    assert "drive" not in target


def test_an_address_on_the_allowed_tab_signs_in_whatever_its_case(client, monkeypatch, sent):
    response = sign_in(client, monkeypatch, "someone@example.com", allowed=["Someone@Example.com "])
    assert response.headers["Location"] == "/"
    assert b"someone@example.com" in client.get("/").data
    assert sent == []


def test_the_operator_is_allowed_without_a_row(client, monkeypatch):
    assert sign_in(client, monkeypatch, "operator@example.com", allowed=[]).headers["Location"] == "/"


def test_an_unknown_address_gets_the_invite_page_and_one_email_naming_it(client, monkeypatch, sent):
    response = sign_in(client, monkeypatch, "stranger@example.com", allowed=["someone@example.com"])
    assert response.headers["Location"] == "/invite"
    assert b"Invite only" in client.get("/invite").data
    assert len(sent) == 1
    to, _, text = sent[0]
    assert to == "operator@example.com" and "stranger@example.com" in text
    with client.session_transaction() as session:
        assert "email" not in session


def test_an_unverified_address_is_refused(client, monkeypatch, sent):
    assert sign_in(client, monkeypatch, "someone@example.com", allowed=["someone@example.com"], verified=False).status_code == 400
    assert sent == []


def test_a_mismatched_state_is_refused(client):
    with client.session_transaction() as session:
        session["state"] = "state-token"
    assert client.get("/auth/callback?code=auth-code&state=other").status_code == 400


def test_pages_behind_the_gate_send_a_stranger_to_sign_in(client):
    assert client.get("/").headers["Location"] == "/signin"
    assert b"Sign in with Google" in client.get("/signin").data
