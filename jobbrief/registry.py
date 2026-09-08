"""The registry sheet: the two tab headers and the run's user selection.

One spreadsheet owned by the service account, the only per-user state outside a user's own
sheet. Rows are written by signup and settings; the run only reads them. The caller reads a
tab with sheet.read_tab and passes the rows here, so this stays offline and testable."""


# Users is the run's roster; Allowed is the web gate's guest list, read only by the app.
USERS_HEADER = ["email", "sheet_id", "active", "frequency", "added"]
ALLOWED_HEADER = ["email", "added"]


def users_to_run(rows, only_users):
    """The active users the run should serve. `rows` are Users-tab dicts as read_tab gives
    them; `only_users` is a collection of emails to restrict to, or empty/None for everyone.
    Email matching is case-insensitive; everyone excluded here is a non-send day. `active` and
    `email` are hand-edited in a spreadsheet, so both are trimmed and `active` lowercased
    before comparing, to keep a stray capital or trailing space from silently dropping a user."""
    allowed = {email.lower() for email in only_users} if only_users else None
    return [
        row for row in rows
        if row.get("active", "").strip().lower() == "yes"
        and (allowed is None or row.get("email", "").strip().lower() in allowed)
    ]
