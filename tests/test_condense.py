from jobbrief.sources import condense
from record_fixtures import REQUIRED_LINES  # the lines the recorder guaranteed the posting carries


def test_condense_keeps_years_remote_and_pay_lines(fixture_dir):
    text = (fixture_dir / "posting.txt").read_text()
    condensed = condense(text)
    assert len(condensed) < len(text)
    for name, pattern in REQUIRED_LINES.items():
        assert pattern.search(condensed), f"condense dropped the {name} line"


def test_a_vocabulary_keeps_the_license_line_the_default_drops():
    license_line = "A valid driver's license and a pesticide applicator certification."
    text = "The district serves the county watershed. " * 80 + license_line
    assert license_line not in condense(text)
    assert license_line in condense(text, vocabulary=["pesticide applicator", "driver's license"])
