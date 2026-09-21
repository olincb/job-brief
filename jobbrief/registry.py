"""The registry sheet: the two tab headers and the run's user selection.

One spreadsheet the operator owns and the service account edits, the only per-user state outside a user's own
sheet. Rows are written by signup and settings; the run only reads them. The caller reads a
tab with sheet.read_tab and passes the rows here, so this stays offline and testable."""


# Users is the run's roster; Allowed is the operator's switch for one person, read at
# sign-in and again by every run.
USERS_HEADER = ["email", "sheet_id", "active", "frequency", "added"]
ALLOWED_HEADER = ["email", "added"]


def users_to_run(rows, allowed_rows, only_users):
    """The users the run should serve: on the `Allowed` tab, `active`, and within `ONLY_USERS`
    when it is set. `rows` and `allowed_rows` are Users and Allowed dicts as read_tab gives
    them; `only_users` is a collection of emails to restrict to, or empty/None for everyone.
    Deleting an `Allowed` row is how the operator switches someone off, so the run checks that
    tab every day rather than trusting the roster alone; putting the row back turns them on
    again with their settings and sheet as they left them. Email matching is case-insensitive; the
    run never reaches anyone excluded here, so nothing is written for them. `active` and `email`
    are hand-edited in a spreadsheet, so both are trimmed and `active` lowercased before comparing,
    to keep a stray capital or trailing space from silently dropping a user."""
    allowed = {row.get("email", "").strip().lower() for row in allowed_rows}
    only = {email.strip().lower() for email in only_users} if only_users else None
    served = []
    for row in rows:
        email = row.get("email", "").strip().lower()
        if row.get("active", "").strip().lower() != "yes" or email not in allowed:
            continue
        if only is None or email in only:
            served.append(row)
    return served
