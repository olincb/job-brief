"""Shared fixtures. The default run makes no network calls: urlopen is replaced for every
test, so code that reaches for a live board fails loudly instead of passing by accident.
Recording fixtures is a separate, deliberate step; see README.md."""

import urllib.request
from pathlib import Path

import pytest

from jobbrief import brief


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("tests must not touch the network; monkeypatch jobbrief.brief.get instead")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)


@pytest.fixture
def fixture_dir():
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def out(monkeypatch, tmp_path):
    """Point the engine's output directory at a scratch directory."""
    monkeypatch.setattr(brief, "OUT", tmp_path)
    return tmp_path
