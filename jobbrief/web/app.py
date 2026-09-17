"""Sign-in, the invite gate, and the pages behind it.

A signed httpOnly cookie holds the signed-in email and nothing else; the OAuth token is
used inside the callback and discarded. Every route but sign-in, the callback, and the
invite page requires a gated session, so a page added later is behind the gate unless it
is named here."""

import os
import secrets
import time
from html import escape

from flask import Flask, abort, redirect, render_template, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from jobbrief import mail, sheet
from jobbrief.web import oauth


SCOPES = ["openid", "email", "profile"]  # drive.file is asked for at signup, in its own consent step
OPEN_ENDPOINTS = {"signin", "login", "callback", "invite", "static"}
ALLOWED_TTL = 60  # seconds; a woken Machine pays one Sheets call for a burst of requests, not one each

_allowed = (0.0, frozenset())


def allowed_emails():
    """The `Allowed` tab as lowercased addresses, re-read at most once every ALLOWED_TTL."""
    global _allowed
    fetched_at, emails = _allowed
    if time.time() - fetched_at > ALLOWED_TTL:
        token = sheet.service_account_token(os.environ["SERVICE_ACCOUNT_JSON"])
        rows = sheet.read_tab(token, os.environ["REGISTRY_SHEET_ID"], "Allowed")
        emails = frozenset(row.get("email", "").strip().lower() for row in rows)
        _allowed = (time.time(), emails)
    return emails


def is_allowed(email):
    """`email` must already be lowercased; the operator is a deployment value and always in."""
    return email == os.environ["OPERATOR_EMAIL"].strip().lower() or email in allowed_emails()


def notify_operator(email):
    """Tell the operator someone uninvited stopped by. Approval is adding a row to `Allowed`."""
    text = f"{email} signed in and is not on the Allowed tab. Add a row there to let them in."
    mail.send(os.environ["OPERATOR_EMAIL"], "Job brief: sign-in from an uninvited address",
              f"<p>{escape(text)}</p>", text)


def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ["SESSION_KEY"]
    # Fly terminates TLS and forwards the scheme; without this the redirect URI is built as
    # http and does not match the one registered on the OAuth client.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1)

    @app.before_request
    def require_gated_session():
        if request.endpoint not in OPEN_ENDPOINTS and not session.get("email"):
            return redirect(url_for("signin"))

    @app.get("/")
    def home():
        return render_template("home.html", email=session["email"])

    @app.get("/signin")
    def signin():
        return render_template("signin.html")

    @app.get("/invite")
    def invite():
        return render_template("invite.html")

    @app.get("/auth/login")
    def login():
        session["state"] = secrets.token_urlsafe(16)
        return redirect(oauth.auth_url(os.environ["GOOGLE_CLIENT_ID"],
                                       url_for("callback", _external=True), SCOPES, session["state"]))

    @app.get("/auth/callback")
    def callback():
        state = session.pop("state", None)
        if not state or request.args.get("state") != state:
            abort(400)
        token = oauth.exchange_code(request.args.get("code", ""), url_for("callback", _external=True),
                                    os.environ["GOOGLE_CLIENT_ID"], os.environ["GOOGLE_CLIENT_SECRET"])
        email = oauth.verified_email(token)
        if not email:
            abort(400)
        email = email.strip().lower()
        if not is_allowed(email):
            notify_operator(email)
            return redirect(url_for("invite"))
        session["email"] = email
        return redirect(url_for("home"))

    return app
