import pytest

from jobbrief.sources import condense
from record_fixtures import REQUIRED_LINES  # the lines the recorder guaranteed the posting carries


def test_condense_keeps_years_remote_and_pay_lines(fixture_dir):
    text = (fixture_dir / "posting.txt").read_text()
    condensed = condense(text)
    assert len(condensed) < len(text)
    for name, pattern in REQUIRED_LINES.items():
        assert pattern.search(condensed), f"condense dropped the {name} line"


# The vocabulary replaces the software stack tier, so a stack word it omits is dropped.
@pytest.mark.parametrize("vocabulary, sentence, kept", [
    (["pesticide applicator", "driver's license"], "A valid driver's license and a pesticide applicator certification.", True),
    (["C++"], "The pipeline code is written in C++ for speed.", True),
    (["GIS"], "The cluster runs on kubernetes in the county data center.", False),
])
def test_a_vocabulary_stands_in_for_the_stack_tier(vocabulary, sentence, kept):
    text = "The district serves the county watershed. " * 80 + sentence
    assert (sentence in condense(text, vocabulary=vocabulary)) is kept
