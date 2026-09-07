import argparse
import json
from datetime import date, timedelta

from jobbrief import brief
from jobbrief.brief import RUNS_HEADER


def heartbeat(out, capsys, runs):
    """Run the heartbeat rule over a synthetic Runs tab. `runs` is (days ago, emailed)."""
    rows = [[str(date.today() - timedelta(days=ago)), "", "", "", "", emailed, "", ""] for ago, emailed in runs]
    (out / "runs.json").write_text(json.dumps({"values": [RUNS_HEADER, *rows]}))
    brief.cmd_heartbeat(argparse.Namespace(out=out, runs="", postings="", days=4))
    return capsys.readouterr().out.strip()


def test_empty_runs_tab_sends(out, capsys):
    assert heartbeat(out, capsys, []) == "send"
    assert "Still running" in (out / "heartbeat.html").read_text()


def test_two_quiet_days_stay_quiet(out, capsys):
    assert heartbeat(out, capsys, [(2, "yes"), (1, "no")]) == "quiet"


def test_four_quiet_days_send(out, capsys):
    # Rows with emailed=no are runs that stayed silent; they do not reset the clock.
    assert heartbeat(out, capsys, [(4, "yes"), (3, "no"), (2, "no"), (1, "no")]) == "send"
