"""Google's OAuth authorization-code flow for a confidential client, over urllib.

Sign-in asks for identity scopes and signup asks for `drive.file` in a second consent
step, so scopes and the redirect URI are the caller's to name."""

import json
import urllib.parse
import urllib.request


AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"


def request(url, data=None, token=None):
    """One JSON request; the whole network surface of this module goes through here.
    HTTPError propagates."""
    body = urllib.parse.urlencode(data).encode() if data else None
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=headers)) as response:
        return json.load(response)


def auth_url(client_id, redirect_uri, scopes, state, login_hint=None):
    """Where to send the browser to start a consent step. `login_hint` names the account
    already signed in, so the second step is one click rather than an account chooser."""
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "state": state,
        "access_type": "online",  # no token outlives the request that got it, so no refresh token is asked for
    }
    if login_hint:
        params["login_hint"] = login_hint
    return AUTH + "?" + urllib.parse.urlencode(params)


def exchange_code(code, redirect_uri, client_id, client_secret):
    """Swap an authorization code for an access token."""
    return request(TOKEN, {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    })["access_token"]


def verified_email(access_token):
    """The signed-in address, or None when Google has not verified it. Reading userinfo
    rather than decoding the id token keeps JWT verification out of the app."""
    info = request(USERINFO, token=access_token)
    return info["email"] if info.get("email_verified") else None
