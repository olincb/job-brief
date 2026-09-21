"""The daily run: the date rules over a user's registry row and Runs tab.

Dates are stamped in UTC everywhere, so these are plain date arithmetic."""

from jobbrief.sheet import days_since_email


def is_send_day(frequency, today):
    """Whether today is a send day on `frequency`: the registry's `daily`, `weekdays`
    or `weekly`."""
    if frequency == "weekdays":
        return today.weekday() < 5
    if frequency == "weekly":
        return today.weekday() == 0
    return True


def lookback(default_days, runs_rows, today):
    """How many days of postings this run covers: `default_days`, stretched to the gap
    since the last emailed brief so a missed or failed run loses no day. With no email on
    record, the default."""
    gap = days_since_email(runs_rows, today)
    return max(default_days, gap) if gap is not None else default_days
