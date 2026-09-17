"""Sign-in, the invite gate, signup, and the pages behind it.

A signed httpOnly cookie holds the signed-in email and nothing else; an OAuth token is
used inside the callback that got it and discarded. Every route but sign-in, the sign-in
callback, and the invite page requires a gated session, so a page added later is behind
the gate unless it is named here."""

import json
import os
import secrets
import time
from datetime import datetime
from html import escape

from flask import Flask, abort, redirect, render_template, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from jobbrief import mail, profile, registry, sheet
from jobbrief.web import form, oauth


SCOPES = ["openid", "email", "profile"]  # drive.file is asked for at signup, in its own consent step
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]
OPEN_ENDPOINTS = {"signin", "login", "callback", "invite", "static"}
ALLOWED_TTL = 60  # seconds; a woken Machine pays one Sheets call for a burst of requests, not one each
STASH_TTL = 3600  # seconds; an abandoned signup should not sit in memory for the life of the process
SHEET_TITLE = "Job Brief"
SHEET_URL = "https://docs.google.com/spreadsheets/d/{}"
# The daily run's models and retry budget, which `jobbrief.cli` takes as flag defaults.
MODELS = ["gemini-3.8-flash", "gemini-3.5-flash"]
RETRIES = 8

_allowed = (0.0, frozenset())
_stash = {}


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


def registry_row(email):
    """This user's `Users` row, or None when they have not signed up. Not cached: a user
    who has just finished signup must not be sent back to the form."""
    token = sheet.service_account_token(os.environ["SERVICE_ACCOUNT_JSON"])
    rows = sheet.read_tab(token, os.environ["REGISTRY_SHEET_ID"], "Users")
    return next((row for row in rows if row.get("email", "").strip().lower() == email), None)


def notify_operator(email):
    """Tell the operator someone uninvited stopped by. Approval is adding a row to `Allowed`."""
    text = f"{email} signed in and is not on the Allowed tab. Add a row there to let them in."
    mail.send(os.environ["OPERATOR_EMAIL"], "Job brief: sign-in from an uninvited address",
              f"<p>{escape(text)}</p>", text)


def stash(key, entry):
    """Hold one signup between the form and the Drive consent. In-process, so a restart
    loses it and the user fills the form in again."""
    cutoff = time.time() - STASH_TTL
    for stale in [held for held, value in _stash.items() if value["created"] < cutoff]:
        del _stash[stale]
    _stash[key] = dict(entry, created=time.time())


def create_user_sheet(token, entry):
    """The user's own sheet, made with their seconds-old `drive.file` token: six tabs, the
    answers, the profile, and the mechanical settings, with the service account added as
    editor so the daily run can read and write it. Returns the sheet id."""
    sheet_id = sheet.init_sheet(token, "", SHEET_TITLE)
    sheet.add_editor(token, sheet_id, json.loads(os.environ["SERVICE_ACCOUNT_JSON"])["client_email"], notify=False)
    sheet.append_rows(token, sheet_id, "Answers", [[entry["answers"][column] for column in sheet.ANSWERS_HEADER]])
    sheet.write_range(token, sheet_id, "Profile!A1", [[entry["profile"]]])
    sheet.append_rows(token, sheet_id, "Settings", [[key, value] for key, value in entry["settings"].items()])
    return sheet_id


def register(email, sheet_id):
    """Append the `Users` row through the service account. `active` is `no`: a new user is a
    draft until the operator has read the generated profile."""
    row = {"email": email, "sheet_id": sheet_id, "active": "no", "frequency": "daily",
           "added": datetime.now().strftime("%Y-%m-%d")}
    token = sheet.service_account_token(os.environ["SERVICE_ACCOUNT_JSON"])
    sheet.append_rows(token, os.environ["REGISTRY_SHEET_ID"], "Users",
                      [[row[column] for column in registry.USERS_HEADER]])


def send_welcome(email, sheet_id):
    url = SHEET_URL.format(sheet_id)
    review = ("The operator reads your generated profile before the first brief goes out. "
              "The Profile tab is yours to edit; it is what the ranking reads.")
    mail.send(email, "Your job brief is set up",
              f'<p>Your tracking sheet: <a href="{url}">{url}</a></p><p>{review}</p>',
              f"Your tracking sheet:\n\n{url}\n\n{review}")


def signup_page(message=""):
    return render_template("signup.html", lists=form.LISTS, term_answers=form.TERM_ANSWERS, message=message)


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
        return redirect(url_for("settings") if registry_row(session["email"]) else url_for("signup"))

    @app.get("/signin")
    def signin():
        return render_template("signin.html")

    @app.get("/invite")
    def invite():
        return render_template("invite.html")

    @app.get("/settings")
    def settings():
        row = registry_row(session["email"])
        if not row:
            return redirect(url_for("signup"))
        return render_template("settings.html", sheet_url=SHEET_URL.format(row["sheet_id"]))

    @app.get("/signup")
    def signup():
        if registry_row(session["email"]):
            return redirect(url_for("settings"))
        return signup_page()

    @app.post("/signup")
    def submit():
        """Everything that does not need Drive: the resume is read, the profile drafted, and
        the answers held in memory, so a model failure ends signup with nothing to clean up."""
        upload = request.files.get("resume")
        resume = (upload.filename, upload.read()) if upload and upload.filename else None
        answers = form.answers_from_form(request.form, resume[0] if resume else "",
                                         datetime.now().strftime("%Y-%m-%d"))
        try:
            profile_text, settings = profile.draft_profile(answers, MODELS, os.environ["GEMINI_API_KEY"],
                                                           RETRIES, resume=resume)
        except SystemExit as failure:
            print(f"profile generation failed: {failure}")
            return signup_page("The model could not draft a profile just now. Nothing was created, "
                               "and filling the form in again is all it takes.")
        session["signup"] = secrets.token_urlsafe(16)
        stash(session["signup"], {"answers": answers, "profile": profile_text, "settings": settings})
        session["state"] = secrets.token_urlsafe(16)
        return redirect(oauth.auth_url(os.environ["GOOGLE_CLIENT_ID"], url_for("drive_callback", _external=True),
                                       DRIVE_SCOPES, session["state"], login_hint=session["email"]))

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

    @app.get("/auth/drive/callback")
    def drive_callback():
        state = session.pop("state", None)
        if not state or request.args.get("state") != state:
            abort(400)
        entry = _stash.pop(session.pop("signup", ""), None)
        if not entry:
            return signup_page("Your answers were not here when you came back, so nothing was "
                               "created. Filling the form in again is all it takes.")
        token = oauth.exchange_code(request.args.get("code", ""), url_for("drive_callback", _external=True),
                                    os.environ["GOOGLE_CLIENT_ID"], os.environ["GOOGLE_CLIENT_SECRET"])
        sheet_id = create_user_sheet(token, entry)
        register(session["email"], sheet_id)
        send_welcome(session["email"], sheet_id)
        return redirect(url_for("settings"))

    return app
