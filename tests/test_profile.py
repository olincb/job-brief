import base64
import io
import json
import re
import zipfile

import pytest

from jobbrief import llm
from jobbrief.profile import build_prompt, docx_text, draft_profile, title_filters


# An invented Answers row: the columns the form writes, with several left blank.
ANSWERS = {
    "field": "municipal water systems",
    "experience": "6 years, most recently water quality analyst",
    "resume": "resume.pdf",
    "tools": "GIS - job\nSCADA - class",
    "search_titles": "water quality analyst\nsenior hydrologist",
    "exclude_titles": "sales\nnone",
    "entry_level": "no",
    "about_you": "Happiest with a sampling kit in the truck.",
    "submitted": "2026-09-16",
}

ROLE_TITLES = {"entry": ["intern", "junior"], "mid": ["associate", "specialist"], "senior": ["senior", "lead"]}


def test_prompt_carries_the_answered_columns_and_the_resume_text():
    text = build_prompt("INSTRUCTIONS", ANSWERS, resume_text="RESUME TEXT")
    positions = [text.index(s) for s in ("INSTRUCTIONS", "## Answers", "### field", "### about_you", "## Resume\n\nRESUME TEXT")]
    assert positions == sorted(positions)
    assert "municipal water systems" in text
    assert "### certifications" not in text
    assert "resume.pdf" not in text and "2026-09-16" not in text


@pytest.mark.parametrize("experience, widened", [
    ("1 year, plus a summer internship", ["intern hydrologist", "junior hydrologist"]),
    ("6 years, most recently water quality analyst", ["associate hydrologist", "specialist hydrologist"]),
    ("12 years leading a water team", ["lead hydrologist"]),
])
def test_a_leading_level_word_widens_to_the_band_the_years_put_the_user_in(experience, widened):
    answers = dict(ANSWERS, experience=experience, search_titles="senior hydrologist")
    filters, _ = title_filters(answers, ROLE_TITLES)
    assert filters == ["senior hydrologist"] + [rf"\b{phrase}\b" for phrase in widened]


def test_a_level_word_that_is_not_the_leading_one_is_left_alone():
    filters, _ = title_filters(dict(ANSWERS, search_titles="team lead\nwater quality analyst"), ROLE_TITLES)
    assert filters == ["team lead", "water quality analyst"]


@pytest.mark.parametrize("entry_level", ["yes", "maybe", ""])
def test_entry_titles_are_kept_unless_a_role_below_training_is_ruled_out(entry_level):
    _, excludes = title_filters(dict(ANSWERS, entry_level=entry_level), ROLE_TITLES)
    assert excludes == ["sales"]


def test_saying_no_to_a_role_below_training_excludes_the_entry_words():
    _, excludes = title_filters(dict(ANSWERS, entry_level="no"), ROLE_TITLES)
    assert excludes == ["sales", r"\bintern\b", r"\bjunior\b"]


def test_an_excluded_entry_word_does_not_take_the_titles_it_is_a_substring_of():
    _, excludes = title_filters(dict(ANSWERS, entry_level="no"), ROLE_TITLES)
    # Matched the way select_candidates matches them.
    pattern = re.compile("|".join(excludes), re.IGNORECASE)
    assert not pattern.search("International Water Analyst")
    assert not pattern.search("Internal Communications Analyst")
    assert pattern.search("Hydrology Intern")


def test_docx_text_joins_runs_into_paragraphs():
    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    document = (f'<w:document xmlns:w="{namespace}"><w:body>'
                "<w:p><w:r><w:t>Water quality analyst</w:t></w:r><w:r><w:t>, 6 years</w:t></w:r></w:p>"
                "<w:p><w:r><w:t>Sampling and reporting</w:t></w:r></w:p>"
                "<w:p/></w:body></w:document>")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document)
    assert docx_text(buffer.getvalue()) == "Water quality analyst, 6 years\nSampling and reporting"


def test_draft_profile_attaches_a_pdf_resume_and_returns_the_settings(monkeypatch):
    bodies = []

    def call(model, body, api_key, attempts):
        bodies.append(json.loads(body))
        return {"candidates": [{"content": {"parts": [{"text": "## Summary\n\nA water person."}]}}], "usageMetadata": {}}

    monkeypatch.setattr(llm, "call_gemini", call)
    profile, settings = draft_profile(ANSWERS, ["primary"], "key", 2, resume=("resume.pdf", b"%PDF-1.4"))
    parts = bodies[0]["contents"][0]["parts"]
    assert parts[0]["inlineData"] == {"mimeType": "application/pdf", "data": base64.b64encode(b"%PDF-1.4").decode()}
    assert "## Dealbreakers" in parts[1]["text"]
    assert profile == "## Summary\n\nA water person."
    assert settings["title_filter"].splitlines()[0] == "water quality analyst"
    assert r"\bintern\b" in settings["title_exclude"].splitlines()
    assert settings["max_picks"] == 10


def test_a_resume_that_is_neither_a_pdf_nor_a_docx_is_refused():
    with pytest.raises(ValueError):
        draft_profile(ANSWERS, ["primary"], "key", 2, resume=("resume.doc", b"\xd0\xcf\x11\xe0"))
