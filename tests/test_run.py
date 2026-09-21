import json
from datetime import date, timedelta

import pytest

from jobbrief import mail, run
from jobbrief.run import is_send_day, lookback
from jobbrief.sheet import RUNS_HEADER
from jobbrief.sources import SKIPPED, posting

MONDAY = date(2026, 3, 2)
FRIDAY = date(2026, 3, 6)
SATURDAY = date(2026, 3, 7)
SUNDAY = date(2026, 3, 8)
TUESDAY = date(2026, 3, 10)


def runs(*rows):
    """Synthetic Runs rows in the shape read_tab returns; each row is (date, outcome, emailed)."""
    return [dict(zip(RUNS_HEADER, [day, "", "", "", outcome, emailed, "", "", "", ""]))
            for day, outcome, emailed in rows]


def test_daily_sends_on_every_day_of_the_week():
    assert is_send_day("daily", MONDAY)
    assert is_send_day("daily", SUNDAY)


def test_weekdays_sends_monday_to_friday_only():
    assert is_send_day("weekdays", FRIDAY)
    assert not is_send_day("weekdays", SATURDAY)


def test_weekly_sends_on_monday_only():
    assert is_send_day("weekly", MONDAY)
    assert not is_send_day("weekly", TUESDAY)


def test_a_gap_longer_than_the_default_stretches_the_lookback():
    rows = runs(("2026-03-02", "sent", "yes"), ("2026-03-05", "failed", "no"))
    assert lookback(3, rows, SUNDAY) == 6


def test_a_recent_send_leaves_the_lookback_at_the_default():
    rows = runs(("2026-03-06", "sent", "yes"))
    assert lookback(3, rows, SUNDAY) == 3


def test_a_tab_with_no_emailed_row_uses_the_default():
    assert lookback(3, [], SUNDAY) == 3
    assert lookback(3, runs(("2026-01-01", "skipped", "no")), SUNDAY) == 3


# The loop over every user, driven offline. Addresses and sheet ids are invented.
ENV = {"GEMINI_API_KEY": "key", "SERVICE_ACCOUNT_JSON": "{}", "REGISTRY_SHEET_ID": "registry",
       "OPERATOR_EMAIL": "ops@example.com"}
USER_A = {"email": "a@example.com", "sheet_id": "sheet-a", "active": "yes", "frequency": "daily", "added": "2026-01-01"}
USER_B = {"email": "b@example.com", "sheet_id": "sheet-b", "active": "yes", "frequency": "daily", "added": "2026-01-01"}
SETTINGS = [{"key": "title_filter", "value": "engineer"}, {"key": "title_exclude", "value": ""},
            {"key": "lookback_days", "value": "3"}, {"key": "max_picks", "value": "5"}]
POOL = [posting("fake", "board", n, "Backend Engineer", "Remote", f"https://example.com/jobs/{n}", None, "a description")
        for n in range(3)]


@pytest.fixture(autouse=True)
def clear_skipped():
    """SKIPPED is a module global the fetchers fill, so each test starts it empty."""
    SKIPPED.clear()
    yield
    SKIPPED.clear()


def unreachable(slug):
    SKIPPED.append("https://example.com/feed")
    return iter([])


def drive(monkeypatch, users, tabs, picks=0, unreadable=()):
    """Run the loop against fake sheets, one fake pool and no email. `tabs` is keyed by
    (sheet_id, tab); a sheet id in `unreadable` raises on read. Appends, emails and model
    calls land in one list, in the order they happened."""
    events = []
    rows = {("registry", "Users"): users,
            ("registry", "Allowed"): [{"email": user["email"], "added": "2026-01-01"} for user in users]}
    rows.update(tabs)

    def read_tab(token, sheet_id, tab):
        if sheet_id in unreadable:
            raise RuntimeError("the sheet is gone")
        return [dict(row) for row in rows.get((sheet_id, tab), [])]

    def generate(prompt, models, api_key, retries, json_output=False):
        events.append(("generate", "", ""))
        chosen = [{"id": job["id"], "fit": 4, "reason": "why"} for job in POOL[:picks]]
        return json.dumps({"picks": chosen, "brief_markdown": "## Picks"}), "model-x", {"totalTokenCount": 12}

    monkeypatch.setattr(run, "FETCHERS", {"hackernews": lambda slug: iter(POOL)})  # the one whole-feed slug in the packaged board list
    monkeypatch.setattr(run, "service_account_token", lambda key_json: "token")
    monkeypatch.setattr(run, "read_tab", read_tab)
    monkeypatch.setattr(run, "read_cell", lambda token, sheet_id, cell_range: "a profile")
    monkeypatch.setattr(run, "append_rows", lambda token, sheet_id, tab, new: events.append(("append", sheet_id, tab, new)))
    monkeypatch.setattr(run, "generate", generate)
    monkeypatch.setattr(mail, "send", lambda to, subject, html, text: events.append(("email", to, subject)))
    return events


def tabs_written(events, sheet_id):
    return [event[2] for event in events if event[0] == "append" and event[1] == sheet_id]


def logged_run(events, sheet_id):
    row = next(event[3][0] for event in events if event[0] == "append" and event[1] == sheet_id and event[2] == "Runs")
    return dict(zip(RUNS_HEADER, row))


def recipients(events):
    return [event[1] for event in events if event[0] == "email"]


def test_one_user_failing_leaves_the_next_one_served(monkeypatch):
    events = drive(monkeypatch, [USER_A, USER_B], {("sheet-b", "Settings"): SETTINGS}, picks=1, unreadable=["sheet-a"])
    assert run.run(ENV, FRIDAY) == 0
    served = logged_run(events, "sheet-b")
    assert (served["outcome"], served["emailed"], served["picks"]) == ("sent", "yes", 1)
    failed = logged_run(events, "sheet-a")
    assert (failed["outcome"], failed["emailed"]) == ("failed", "no")
    assert "the sheet is gone" in failed["note"]
    assert recipients(events) == ["ops@example.com", "a@example.com", "b@example.com"]
    # The rows go in only after the send, so the sheet and the inbox always agree.
    assert [event[0] for event in events if event[1] in ("sheet-b", "b@example.com")] == ["email", "append", "append", "append"]
    assert tabs_written(events, "sheet-b") == ["Postings", "Seen", "Runs"]


def test_only_users_limits_the_run_to_the_named_user(monkeypatch):
    tabs = {("sheet-a", "Settings"): SETTINGS, ("sheet-b", "Settings"): SETTINGS}
    events = drive(monkeypatch, [USER_A, USER_B], tabs, picks=1)
    assert run.run(dict(ENV, ONLY_USERS="b@example.com"), FRIDAY) == 0
    assert {event[1] for event in events if event[0] == "append"} == {"sheet-b"}


def test_a_non_send_day_writes_only_a_skipped_row(monkeypatch):
    weekly = dict(USER_A, frequency="weekly")
    events = drive(monkeypatch, [weekly], {("sheet-a", "Settings"): SETTINGS}, picks=1)
    assert run.run(ENV, FRIDAY) == 0
    assert logged_run(events, "sheet-a")["outcome"] == "skipped"
    assert tabs_written(events, "sheet-a") == ["Runs"]
    assert recipients(events) == []


def test_an_empty_title_filter_costs_no_model_call(monkeypatch):
    settings = [dict(row, value="") if row["key"] == "title_filter" else row for row in SETTINGS]
    events = drive(monkeypatch, [USER_A], {("sheet-a", "Settings"): settings}, picks=1)
    assert run.run(ENV, FRIDAY) == 0
    row = logged_run(events, "sheet-a")
    assert (row["outcome"], row["note"]) == ("skipped", "empty filter")
    assert [event for event in events if event[0] == "generate"] == []
    assert recipients(events) == []


def test_no_picks_after_the_quiet_threshold_sends_a_heartbeat(monkeypatch):
    tabs = {("sheet-a", "Settings"): SETTINGS,
            ("sheet-a", "Runs"): runs((str(FRIDAY - timedelta(days=5)), "sent", "yes"))}
    events = drive(monkeypatch, [USER_A], tabs)
    assert run.run(ENV, FRIDAY) == 0
    assert logged_run(events, "sheet-a")["outcome"] == "heartbeat"
    assert recipients(events) == ["a@example.com"]


def test_no_picks_within_the_quiet_threshold_stays_silent(monkeypatch):
    tabs = {("sheet-a", "Settings"): SETTINGS,
            ("sheet-a", "Runs"): runs((str(FRIDAY - timedelta(days=2)), "sent", "yes"))}
    events = drive(monkeypatch, [USER_A], tabs)
    assert run.run(ENV, FRIDAY) == 0
    assert logged_run(events, "sheet-a")["outcome"] == "quiet"
    assert recipients(events) == []


def test_every_source_skipped_stops_the_run_before_any_user(monkeypatch):
    events = drive(monkeypatch, [USER_A], {("sheet-a", "Settings"): SETTINGS}, picks=1)
    monkeypatch.setattr(run, "FETCHERS", {"hackernews": unreachable})
    assert run.run(ENV, FRIDAY) == 1
    assert events == []
