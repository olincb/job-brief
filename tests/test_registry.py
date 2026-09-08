from jobbrief.registry import users_to_run

# Invented rows in the shape read_tab returns: keyed by the Users header.
USERS = [
    {"email": "amy@example.com", "sheet_id": "sheet-amy", "active": "yes", "frequency": "daily", "added": "2026-01-01"},
    {"email": "ben@example.com", "sheet_id": "sheet-ben", "active": "no", "frequency": "weekly", "added": "2026-01-02"},
    {"email": "cat@example.com", "sheet_id": "sheet-cat", "active": "yes", "frequency": "weekdays", "added": "2026-01-03"},
]


def emails(rows):
    return [row["email"] for row in rows]


def test_inactive_rows_are_dropped():
    assert emails(users_to_run(USERS, None)) == ["amy@example.com", "cat@example.com"]


def test_empty_only_users_returns_all_active():
    assert emails(users_to_run(USERS, [])) == ["amy@example.com", "cat@example.com"]


def test_only_users_restricts_case_insensitively():
    assert emails(users_to_run(USERS, ["AMY@EXAMPLE.COM"])) == ["amy@example.com"]


def test_hand_edited_case_and_whitespace_do_not_drop_a_user():
    row = {"email": " dot@example.com ", "sheet_id": "sheet-dot", "active": "Yes ",
           "frequency": "daily", "added": "2026-01-04"}
    assert users_to_run([row], ["dot@example.com"]) == [row]
