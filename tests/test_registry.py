from jobbrief.registry import users_to_run

# Invented rows in the shape read_tab returns: keyed by the Users header.
USERS = [
    {"email": "amy@example.com", "sheet_id": "sheet-amy", "active": "yes", "frequency": "daily", "added": "2026-01-01"},
    {"email": "ben@example.com", "sheet_id": "sheet-ben", "active": "no", "frequency": "weekly", "added": "2026-01-02"},
    {"email": "cat@example.com", "sheet_id": "sheet-cat", "active": "yes", "frequency": "weekdays", "added": "2026-01-03"},
]

# Everyone in USERS is on the Allowed tab, one of them as the operator typed it.
ALLOWED = [{"email": "amy@example.com", "added": "2026-01-01"},
           {"email": " Ben@Example.com ", "added": "2026-01-02"},
           {"email": "cat@example.com", "added": "2026-01-03"}]


def emails(rows):
    return [row["email"] for row in rows]


def test_inactive_rows_are_dropped():
    assert emails(users_to_run(USERS, ALLOWED, None)) == ["amy@example.com", "cat@example.com"]


def test_empty_only_users_returns_all_active():
    assert emails(users_to_run(USERS, ALLOWED, [])) == ["amy@example.com", "cat@example.com"]


def test_only_users_restricts_case_insensitively():
    assert emails(users_to_run(USERS, ALLOWED, ["AMY@EXAMPLE.COM"])) == ["amy@example.com"]


def test_hand_edited_case_and_whitespace_do_not_drop_a_user():
    row = {"email": " dot@example.com ", "sheet_id": "sheet-dot", "active": "Yes ",
           "frequency": "daily", "added": "2026-01-04"}
    allowed = [{"email": "Dot@Example.com", "added": "2026-01-04"}]
    assert users_to_run([row], allowed, ["dot@example.com"]) == [row]


def test_deleting_an_allowed_row_stops_a_user_and_putting_it_back_starts_them_again():
    without_amy = [row for row in ALLOWED if row["email"] != "amy@example.com"]
    assert emails(users_to_run(USERS, without_amy, None)) == ["cat@example.com"]
    assert "amy@example.com" in emails(users_to_run(USERS, ALLOWED, None))
