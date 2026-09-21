"""The daily run: the date rules a user's registry row and Runs tab decide, so the loop
around them stays thin. Dates are UTC everywhere, so these are plain date arithmetic."""

from jobbrief.sheet import days_since_email


def is_send_day(frequency, today):
    """Whether today is a send day for a user on `frequency`, one of the registry's
    `daily`, `weekdays` or `weekly`. Weekly users get their brief on Monday."""
    if frequency == "weekdays":
        return today.weekday() < 5
    if frequency == "weekly":
        return today.weekday() == 0
    return True


def lookback(default_days, runs_rows, today):
    """How many days of postings this run covers: the user's `lookback_days`, stretched to
    the gap since their last emailed brief so a weekly user sees the whole week and a user
    whose run failed yesterday does not lose a day. The first run ever uses the default."""
    gap = days_since_email(runs_rows, today)
    return max(default_days, gap) if gap is not None else default_days
