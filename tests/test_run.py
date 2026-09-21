from datetime import date

from jobbrief.run import is_send_day, lookback
from jobbrief.sheet import RUNS_HEADER

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
