import json

import pytest

from jobbrief import llm
from jobbrief.llm import generate
from jobbrief.rank import build_prompt, finish


def test_prompt_has_the_blocks_the_template_names_in_order():
    text = build_prompt("INSTRUCTIONS", "PROFILE", [{"status": "applied"}], [{"id": "x:y:1"}], 7)
    positions = [text.index(s) for s in ("INSTRUCTIONS", "Maximum picks: 7", "## Candidate profile\n\nPROFILE", "## Pipeline", '"applied"', "## Candidates", '"x:y:1"')]
    assert positions == sorted(positions)


def test_finish_builds_rows_and_drops_invented_ids(capsys):
    candidates = [
        {"id": "gh:a:1", "company": "A", "title": "Engineer", "location": "Remote", "url": "https://example.com/1"},
        {"id": "gh:b:2", "company": "B", "title": "Engineer", "location": "Remote", "url": "https://example.com/2"},
    ]
    result = {"picks": [{"id": "gh:b:2", "fit": 4, "reason": "why", "risk": ""}, {"id": "gh:z:9", "fit": 5}], "brief_markdown": "## Picks"}
    markdown, postings_rows, seen_rows = finish(result, candidates, "2026-09-07")
    assert markdown == "## Picks"
    assert postings_rows == [["2026-09-07", "B", "Engineer", "Remote", "https://example.com/2", 4, "why", "", "", ""]]
    assert seen_rows == [["2026-09-07", "gh:a:1", "https://example.com/1"], ["2026-09-07", "gh:b:2", "https://example.com/2"]]
    assert "invented id gh:z:9" in capsys.readouterr().err


def fake_gemini(monkeypatch, answers):
    """Stand in for call_gemini: `answers` maps model name to a response, None meaning every
    attempt failed. Records what each model was asked."""
    calls = []

    def call(model, body, api_key, attempts):
        calls.append((model, json.loads(body), attempts))
        data = answers[model]
        return {"candidates": [{"content": {"parts": [{"text": data}]}}], "usageMetadata": {"totalTokenCount": 11}} if data else None

    monkeypatch.setattr(llm, "call_gemini", call)
    return calls


def test_generate_falls_back_and_splits_the_retry_budget(monkeypatch):
    calls = fake_gemini(monkeypatch, {"primary": None, "fallback": "answer"})
    text, model, usage = generate("hello", ["primary", "fallback"], "key", 8, json_output=True)
    assert (text, model, usage["totalTokenCount"]) == ("answer", "fallback", 11)
    assert [(m, attempts) for m, _, attempts in calls] == [("primary", 4), ("fallback", 4)]
    assert calls[0][1]["generationConfig"]["responseMimeType"] == "application/json"


def test_generate_prose_leaves_the_response_type_open(monkeypatch):
    calls = fake_gemini(monkeypatch, {"primary": "a profile"})
    assert generate("hello", ["primary"], "key", 3)[0] == "a profile"
    assert "responseMimeType" not in calls[0][1]["generationConfig"]


def test_generate_fails_when_every_model_is_exhausted(monkeypatch):
    fake_gemini(monkeypatch, {"primary": None, "fallback": None})
    with pytest.raises(SystemExit):
        generate("hello", ["primary", "fallback"], "key", 2)
