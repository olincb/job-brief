"""Sign-in, the invite gate, signup, and settings.

A signed httpOnly cookie holds the signed-in email and nothing else; an OAuth token is
used inside the callback that got it and discarded. Every route but sign-in, the sign-in
callback, and the invite page requires a gated session, so a page added later is behind
the gate unless it is named here. Once a user has a sheet, the service account does the
reading and writing: the user's own token is asked for once, to create it."""

import json
import os
import secrets
import time
from datetime import datetime
from html import escape

from flask import Flask, abort, redirect, render_template, request, session, url_for

from jobbrief import llm, mail, profile, registry, sheet
from jobbrief.web import form, oauth


SCOPES = ["openid", "email", "profile"]  # drive.file is asked for at signup, in its own consent step
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]
# signout is open so that a session whose `Allowed` row has since been deleted can still end itself.
OPEN_ENDPOINTS = {"signin", "login", "callback", "invite", "signout", "static"}
ALLOWED_TTL = 60  # seconds; a woken Machine pays one Sheets call for a burst of requests, not one each
STASH_TTL = 3600  # seconds; an abandoned signup should not sit in memory for the life of the process
SHEET_TITLE = "Job Brief"
SHEET_URL = "https://docs.google.com/spreadsheets/d/{}"
RECENT_RUNS = 10
EDITABLE_SETTINGS = ["title_filter", "title_exclude", "max_picks"]  # lookback_days stays as signup wrote it
# A retake redraws the answer-derived settings; the pick cap and lookback stay where the user left them.
RETAKE_SETTINGS = ["title_filter", "title_exclude", "vocabulary"]

_allowed = (0.0, frozenset())
_stash = {}


def bot_token():
    """A service-account token. One per request that needs the registry or a user's sheet."""
    return sheet.service_account_token(os.environ["SERVICE_ACCOUNT_JSON"])


def service_account_email():
    return json.loads(os.environ["SERVICE_ACCOUNT_JSON"])["client_email"]


def today():
    return datetime.now().strftime("%Y-%m-%d")


def allowed_emails():
    """The `Allowed` tab as lowercased addresses, re-read at most once every ALLOWED_TTL."""
    global _allowed
    fetched_at, emails = _allowed
    if time.time() - fetched_at > ALLOWED_TTL:
        rows = sheet.read_tab(bot_token(), os.environ["REGISTRY_SHEET_ID"], "Allowed")
        emails = frozenset(row.get("email", "").strip().lower() for row in rows)
        _allowed = (time.time(), emails)
    return emails


def is_allowed(email):
    """The `Allowed` tab is the whole rule, the operator's own row included. `email` must
    already be lowercased."""
    return email in allowed_emails()


def user_row(token, email):
    """This user's `Users` row and the grid row it sits on, or `(None, 0)` when they have
    not signed up. Not cached: a user who has just finished signup, or just paused, must
    not be shown the state they left. The row number is what a save writes back to."""
    rows = sheet.read_tab(token, os.environ["REGISTRY_SHEET_ID"], "Users")
    for number, row in enumerate(rows, start=2):  # row 1 is the header
        if row.get("email", "").strip().lower() == email:
            return row, number
    return None, 0


def notify_operator(email):
    """Tell the operator someone uninvited stopped by. Approval is adding a row to `Allowed`."""
    url = SHEET_URL.format(os.environ["REGISTRY_SHEET_ID"])
    said = f"{email} signed in and is not on the Allowed tab. To let them in, add a row:"
    mail.send(os.environ["OPERATOR_EMAIL"], "Job brief: sign-in from an uninvited address",
              f'<p>{escape(said)} <a href="{url}">{url}</a></p>', f"{said}\n\n{url}")


def stash(key, entry):
    """Hold one signup between the form and the Drive consent. In-process, so a restart
    loses it and the user fills the form in again."""
    cutoff = time.time() - STASH_TTL
    for stale in [held for held, value in _stash.items() if value["created"] < cutoff]:
        del _stash[stale]
    _stash[key] = dict(entry, created=time.time())


def write_settings(token, sheet_id, updates):
    """Merge values into the `Settings` tab, which is key and value from row 2 down. Read
    first so a key this page does not offer keeps whatever it holds."""
    values = {row["key"]: row["value"] for row in sheet.read_tab(token, sheet_id, "Settings")}
    values.update(updates)
    sheet.write_range(token, sheet_id, "Settings!A2", [[key, value] for key, value in values.items()])


def write_signup(token, sheet_id, answers, profile_text, settings):
    """The three tabs a signup and a retake both write. `Answers` holds one row, so row 2 is
    overwritten rather than appended to, and `Postings`, `Seen` and `Runs` are never touched."""
    sheet.write_range(token, sheet_id, "Answers!A2", [[answers[column] for column in sheet.ANSWERS_HEADER]])
    sheet.write_range(token, sheet_id, "Profile!A1", [[profile_text]])
    write_settings(token, sheet_id, settings)


def create_user_sheet(token, entry):
    """The user's own sheet, made with their seconds-old `drive.file` token: six tabs, their
    answers, and the service account as editor so the daily run can read and write it."""
    sheet_id = sheet.init_sheet(token, "", SHEET_TITLE)
    sheet.add_editor(token, sheet_id, service_account_email(), notify=False)
    write_signup(token, sheet_id, entry["answers"], entry["profile"], entry["settings"])
    return sheet_id


def register(token, email, sheet_id):
    """Append the `Users` row. `active` says the user wants briefs, which they do at signup;
    the pause checkbox is the only thing that ever writes it again."""
    row = {"email": email, "sheet_id": sheet_id, "active": "yes", "frequency": "daily", "added": today()}
    sheet.append_rows(token, os.environ["REGISTRY_SHEET_ID"], "Users",
                      [[row[column] for column in registry.USERS_HEADER]])


def send_welcome(email, sheet_id):
    url = SHEET_URL.format(sheet_id)
    settings_url = url_for("settings", _external=True)
    first = (f"Your first brief goes out on the next send day, and the profile drafted from your "
             f"answers is yours to read and edit in settings: {settings_url}")
    mail.send(email, "Your job brief is set up",
              f'<p>Your tracking sheet: <a href="{url}">{url}</a></p><p>{first}</p>',
              f"Your tracking sheet:\n\n{url}\n\n{first}")


def signup_page(answers=None, message="", retake=False):
    """The questionnaire, filled in from an `Answers` row: the user's own on a retake, what
    they just submitted when the model failed on them, and the offered lists otherwise."""
    answers = answers or form.blank_answers()
    return render_template("signup.html", answers=answers, controls=form.controls(answers),
                           lists=form.LISTS, term_answers=form.TERM_ANSWERS,
                           message=message, retake=retake)


def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ["SESSION_KEY"]
    # Fly terminates TLS and forwards the scheme; without this the redirect URI is built as
    # http and does not match the one registered on the OAuth client.

    @app.before_request
    def require_gated_session():
        """The gate is checked on every request, not just at sign-in, so deleting someone's
        `Allowed` row ends the session they already have rather than waiting for the cookie."""
        if request.endpoint in OPEN_ENDPOINTS:
            return None
        email = session.get("email")
        if not email:
            return redirect(url_for("signin"))
        if not is_allowed(email):
            return redirect(url_for("invite"))

    @app.get("/")
    def home():
        row, _ = user_row(bot_token(), session["email"])
        return redirect(url_for("settings") if row else url_for("signup"))

    @app.get("/signin")
    def signin():
        return render_template("signin.html")

    @app.get("/invite")
    def invite():
        return render_template("invite.html")

    @app.post("/signout")
    def signout():
        session.clear()
        return redirect(url_for("signin"))

    @app.get("/settings")
    def settings():
        token = bot_token()
        row, _ = user_row(token, session["email"])
        if not row:
            return redirect(url_for("signup"))
        sheet_id = row["sheet_id"]
        values = {entry["key"]: entry["value"] for entry in sheet.read_tab(token, sheet_id, "Settings")}
        runs = sheet.read_tab(token, sheet_id, "Runs")[-RECENT_RUNS:][::-1]
        return render_template("settings.html", sheet_url=SHEET_URL.format(sheet_id),
                               profile=sheet.read_cell(token, sheet_id, "Profile!A1"), settings=values,
                               frequency=row.get("frequency", ""),
                               paused=row.get("active", "").strip().lower() != "yes",
                               runs=runs, run_columns=sheet.RUNS_HEADER,
                               max_picks_default=sheet.MAX_PICKS_DEFAULT)

    @app.post("/settings")
    def save():
        """Frequency and pause live on the registry row so the run can skip a non-send day
        without opening the sheet; everything else is the user's sheet."""
        token = bot_token()
        row, number = user_row(token, session["email"])
        if not row:
            return redirect(url_for("signup"))
        sheet.write_range(token, row["sheet_id"], "Profile!A1", [[request.form.get("profile", "")]])
        write_settings(token, row["sheet_id"], {key: request.form.get(key, "") for key in EDITABLE_SETTINGS})
        updated = dict(row, frequency=request.form.get("frequency", row.get("frequency", "")),
                       active="no" if request.form.get("paused") else "yes")
        sheet.write_range(token, os.environ["REGISTRY_SHEET_ID"], f"Users!A{number}",
                          [[updated[column] for column in registry.USERS_HEADER]])
        return redirect(url_for("settings"))

    @app.get("/signup")
    def signup():
        token = bot_token()
        row, _ = user_row(token, session["email"])
        if not row:
            return signup_page()
        answers = sheet.read_tab(token, row["sheet_id"], "Answers")
        return signup_page(answers[0] if answers else None, retake=True)

    @app.post("/signup")
    def submit():
        """Everything that does not need Drive: the resume is read and the profile drafted
        before anything is written, so a model failure costs the user nothing but the wait.
        A user who already has a sheet is retaking the questionnaire, and writes to it."""
        token = bot_token()
        row, _ = user_row(token, session["email"])
        upload = request.files.get("resume")
        resume = (upload.filename, upload.read()) if upload and upload.filename else None
        answers = form.answers_from_form(request.form, resume[0] if resume else "", today())
        try:
            profile_text, settings = profile.draft_profile(answers, llm.MODELS, os.environ["GEMINI_API_KEY"],
                                                           llm.RETRIES, resume=resume)
        except (llm.ModelError, ValueError) as failure:
            print(f"signup could not draft a profile: {failure}")
            notice = ("That resume is not a PDF or a .docx." if isinstance(failure, ValueError)
                      else "The model could not draft a profile just now.")
            return signup_page(answers, f"{notice} Nothing was written, and submitting again is all "
                               "it takes.", retake=bool(row))
        if row:
            write_signup(token, row["sheet_id"], answers, profile_text,
                         {key: settings[key] for key in RETAKE_SETTINGS})
            return redirect(url_for("settings"))
        session["signup"] = secrets.token_urlsafe(16)
        stash(session["signup"], {"answers": answers, "profile": profile_text, "settings": settings})
        session["state"] = secrets.token_urlsafe(16)
        return redirect(oauth.auth_url(os.environ["GCP_OAUTH_CLIENT_ID"], url_for("drive_callback", _external=True),
                                       DRIVE_SCOPES, session["state"], login_hint=session["email"]))

    @app.get("/delete")
    def confirm_delete():
        return render_template("delete.html")

    @app.post("/delete")
    def delete():
        token = bot_token()
        row, number = user_row(token, session["email"])
        if row:
            # The roster first: a user the run cannot serve must not be left on it.
            sheet.delete_row(token, os.environ["REGISTRY_SHEET_ID"], "Users", number)
            sheet.remove_editor(token, row["sheet_id"], service_account_email())
        session.clear()
        return render_template("removed.html")

    @app.get("/auth/login")
    def login():
        session["state"] = secrets.token_urlsafe(16)
        return redirect(oauth.auth_url(os.environ["GCP_OAUTH_CLIENT_ID"],
                                       url_for("callback", _external=True), SCOPES, session["state"]))

    @app.get("/auth/callback")
    def callback():
        state = session.pop("state", None)
        if not state or request.args.get("state") != state:
            abort(400)
        token = oauth.exchange_code(request.args.get("code", ""), url_for("callback", _external=True),
                                    os.environ["GCP_OAUTH_CLIENT_ID"], os.environ["GCP_OAUTH_CLIENT_SECRET"])
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
            return signup_page(message="Your answers were not here when you came back, so nothing was "
                                       "created. Filling the form in again is all it takes.")
        token = oauth.exchange_code(request.args.get("code", ""), url_for("drive_callback", _external=True),
                                    os.environ["GCP_OAUTH_CLIENT_ID"], os.environ["GCP_OAUTH_CLIENT_SECRET"])
        sheet_id = create_user_sheet(token, entry)
        register(bot_token(), session["email"], sheet_id)
        send_welcome(session["email"], sheet_id)
        return redirect(url_for("settings"))

    return app
