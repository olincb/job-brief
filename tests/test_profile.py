import base64
import io
import json
import zipfile

import pytest

from jobbrief import llm
from jobbrief.profile import PROFILE_SCHEMA, build_prompt, docx_text, draft_profile, title_filters
from jobbrief.sheet import MAX_PICKS_DEFAULT


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

def test_prompt_carries_the_answered_columns_and_the_resume_text():
    text = build_prompt("INSTRUCTIONS", ANSWERS, resume_text="RESUME TEXT")
    positions = [text.index(s) for s in ("INSTRUCTIONS", "## Answers", "### field", "### about_you", "## Resume\n\nRESUME TEXT")]
    assert positions == sorted(positions)
    assert "municipal water systems" in text
    assert "### certifications" not in text
    assert "resume.pdf" not in text and "2026-09-16" not in text


def test_the_title_filters_are_the_phrases_as_typed():
    filters, excludes = title_filters(ANSWERS)
    assert filters == ["water quality analyst", "senior hydrologist"]
    assert excludes == ["sales"]  # "none" is an answer, not a phrase


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


# A drafting reply in the schema's shape, with invented phrases.
REPLY = {"profile_markdown": "## Summary\n\nA water person.",
         "title_keywords": ["Water Resources Engineer", "Hydrologist (Senior)"],
         "title_excludes": ["Sales Engineer"],
         "vocabulary": ["Water Distribution Operator", "WDM"]}


def replying(monkeypatch, text):
    """Serve `text` as every model reply and return the request bodies sent."""
    bodies = []

    def call(model, body, api_key, attempts):
        bodies.append(json.loads(body))
        return {"candidates": [{"content": {"parts": [{"text": text}]}}], "usageMetadata": {}}

    monkeypatch.setattr(llm, "call_gemini", call)
    return bodies


def test_draft_profile_attaches_a_pdf_resume_and_returns_the_settings(monkeypatch):
    bodies = replying(monkeypatch, json.dumps(REPLY))
    answers = dict(ANSWERS,
                   tools="GIS - job\nscikit-learn - job\nNode.js: daily\nC++ (class)\n"
                         "CI/CD - at work\nGoogle Sheets - daily\nR — heard of",
                   certifications="Class B CDL\n*pesticide applicator")
    profile, settings = draft_profile(answers, ["primary"], "key", 2, resume=("resume.pdf", b"%PDF-1.4"))
    parts = bodies[0]["contents"][0]["parts"]
    assert parts[0]["inlineData"] == {"mimeType": "application/pdf", "data": base64.b64encode(b"%PDF-1.4").decode()}
    assert "## Dealbreakers" in parts[1]["text"]
    assert bodies[0]["generationConfig"]["responseJsonSchema"] == PROFILE_SCHEMA
    assert profile == "## Summary\n\nA water person."
    assert settings["title_filter"].splitlines()[0] == "water quality analyst"
    assert settings["title_exclude"].splitlines() == ["sales"]
    assert settings["vocabulary"].splitlines() == ["GIS", "scikit-learn", "Node.js", "C++", "CI/CD",
                                                    "Google Sheets", "R", "Class B CDL", "pesticide applicator"]
    # The model's parentheses are escaped so the phrase matches literally.
    assert settings["supplemental_titles"].splitlines() == ["Water Resources Engineer", r"Hydrologist \(Senior\)"]
    assert settings["supplemental_excludes"] == "Sales Engineer"
    assert settings["supplemental_vocabulary"].splitlines() == ["Water Distribution Operator", "WDM"]
    assert settings["max_picks"] == MAX_PICKS_DEFAULT


def test_a_reply_that_is_not_the_schemas_json_fails_the_draft(monkeypatch):
    replying(monkeypatch, "## Summary\n\nA water person.")
    with pytest.raises(llm.ModelError):
        draft_profile(ANSWERS, ["primary"], "key", 2)


def test_a_resume_that_is_neither_a_pdf_nor_a_docx_is_refused():
    with pytest.raises(ValueError):
        draft_profile(ANSWERS, ["primary"], "key", 2, resume=("resume.doc", b"\xd0\xcf\x11\xe0"))
