from jobbrief.brief import condense
from record_fixtures import REQUIRED_LINES  # the lines the recorder guaranteed the posting carries


def test_condense_keeps_years_remote_and_pay_lines(fixture_dir):
    text = (fixture_dir / "posting.txt").read_text()
    condensed = condense(text)
    assert len(condensed) < len(text)
    for name, pattern in REQUIRED_LINES.items():
        assert pattern.search(condensed), f"condense dropped the {name} line"
