"""Signup's model stage: the prose profile drafted from one user's questionnaire answers
and resume, the title filters and condense vocabulary derived from the same answers, and
the model's suggested additions to both. It runs before anything exists in the user's
Drive, so a model failure ends signup with nothing to clean up."""

import io
import json
import re
import zipfile
from importlib.resources import files
from xml.etree import ElementTree

from jobbrief.llm import ModelError, generate
from jobbrief.sheet import ANSWERS_HEADER, LOOKBACK_DAYS_DEFAULT, MAX_PICKS_DEFAULT


DATA = files("jobbrief.data")

# The Answers columns the model is shown: the resume filename and the submit date say
# nothing about the person.
ASKED = [column for column in ANSWERS_HEADER if column not in ("resume", "submitted")]

# The drafting reply; its lists add to the typed title filters and condense vocabulary.
PROFILE_SCHEMA = {
    "type": "object",
    "properties": {
        "profile_markdown": {"type": "string"},
        "title_keywords": {"type": "array", "items": {"type": "string"}},
        "title_excludes": {"type": "array", "items": {"type": "string"}},
        "vocabulary": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["profile_markdown", "title_keywords", "title_excludes", "vocabulary"],
}

WORD_XML = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# Question 4's spaced separator and the level after it; punctuation inside a name, as in
# "scikit-learn" or "Node.js", has no whitespace around it and stays.
TOOL_LEVEL = re.compile(r"(?:\s[-–—|]\s|[:;]\s|\s[([]).*$")


def docx_text(data):
    """The text of a Word file: a .docx is a zip whose `word/document.xml` holds the runs,
    grouped into paragraphs."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        document = ElementTree.fromstring(archive.read("word/document.xml"))
    paragraphs = ("".join(run.text or "" for run in para.iter(f"{WORD_XML}t"))
                  for para in document.iter(f"{WORD_XML}p"))
    return "\n".join(line for line in paragraphs if line.strip())


def _phrases(cell):
    """A list answer as the form writes it, one phrase per line; "none" is an answer, not a
    phrase."""
    lines = [line.strip() for line in (cell or "").splitlines()]
    return [line for line in lines if line and line.lower() != "none"]


def _pattern(phrase):
    """A phrase as one regex line of a Settings cell: escaped so `C++` is literal,
    with spaces left alone so the line stays readable to whoever edits it."""
    return re.escape(phrase).replace("\\ ", " ")


def title_filters(answers):
    """Questions 7 and 8 as the two regex lists Settings holds: the phrases the user typed,
    one per line. What a title word says about seniority is a judgment, and one that reads
    differently in every field, so the profile carries it and these stay literal."""
    return ([_pattern(phrase) for phrase in _phrases(answers.get("search_titles"))],
            [_pattern(phrase) for phrase in _phrases(answers.get("exclude_titles"))])


def build_prompt(template, answers, resume_text=""):
    """The request text: the generator instructions, the answers under the column names the
    template explains, and a Word resume's text. A PDF resume rides along as an attachment
    instead."""
    filled = "\n\n".join(f"### {column}\n{answers[column]}" for column in ASKED if answers.get(column))
    blocks = [template, "## Answers\n\n" + filled]
    if resume_text:
        blocks.append("## Resume\n\n" + resume_text)
    return "\n\n".join(blocks)


def draft_profile(answers, models, api_key, retries, resume=None):
    """One signup's Profile text and the Settings values to write beside it, from an Answers
    row and an optional resume as `(filename, bytes)`. Raises ValueError for a resume that is
    neither a PDF nor a .docx, and ModelError when every model attempt fails or the reply is
    not the schema's JSON; both leave signup with nothing written."""
    pdf, resume_text = None, ""
    if resume:
        filename, data = resume
        if filename.lower().endswith(".pdf"):
            pdf = data
        elif filename.lower().endswith(".docx"):
            resume_text = docx_text(data)
        else:
            raise ValueError(f"{filename} is not a PDF or a .docx")
    prompt = build_prompt(DATA.joinpath("profile_prompt.md").read_text(), answers, resume_text)
    text, _model, _usage = generate(prompt, models, api_key, retries, pdf=pdf, schema=PROFILE_SCHEMA)
    try:
        reply = json.loads(text)
        profile = reply["profile_markdown"]
        titles, excludes, terms = (_phrases("\n".join(reply[key]))
                                   for key in ("title_keywords", "title_excludes", "vocabulary"))
    except (ValueError, KeyError) as exc:
        raise ModelError(f"the drafted profile was not the requested JSON: {exc}") from exc
    title_filter, title_exclude = title_filters(answers)
    # A starred license is one the user would get, so a posting's line naming it matters too.
    vocabulary = ([TOOL_LEVEL.sub("", phrase) for phrase in _phrases(answers.get("tools"))]
                  + [phrase.strip("* ") for phrase in _phrases(answers.get("certifications"))])
    return profile, {
        "title_filter": "\n".join(title_filter),
        "title_exclude": "\n".join(title_exclude),
        "vocabulary": "\n".join(vocabulary),
        "suggested_titles": "\n".join(_pattern(phrase) for phrase in titles),
        "suggested_excludes": "\n".join(_pattern(phrase) for phrase in excludes),
        "suggested_vocabulary": "\n".join(terms),
        "lookback_days": LOOKBACK_DAYS_DEFAULT,
        "max_picks": MAX_PICKS_DEFAULT,
    }
