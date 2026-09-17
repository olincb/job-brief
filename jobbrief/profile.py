"""Signup's model stage: the prose profile drafted from one user's questionnaire answers
and resume, and the title filters derived from the same answers. It runs before anything
exists in the user's Drive, so a model failure ends signup with nothing to clean up."""

import io
import json
import re
import zipfile
from importlib.resources import files
from xml.etree import ElementTree

from jobbrief.llm import generate
from jobbrief.sheet import ANSWERS_HEADER


DATA = files("jobbrief.data")

# The Answers columns the model is shown: the resume filename and the submit date say
# nothing about the person.
ASKED = [column for column in ANSWERS_HEADER if column not in ("resume", "submitted")]

# Years of experience at which each band of level words starts, most experience first.
BANDS = [(8, "senior"), (2, "mid"), (0, "entry")]

WORD_XML = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# The rest of Settings at signup; the user changes both in settings later.
LOOKBACK_DAYS = 3
MAX_PICKS = 10


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


def _years(experience):
    """The number in the answer to question 2, which is a number and a phrase."""
    match = re.search(r"\d+", experience or "")
    return int(match.group()) if match else 0


def _pattern(phrase):
    """A typed phrase as one regex line of a Settings cell: escaped so `C++` is literal,
    with spaces left alone so the line stays readable to whoever edits it."""
    return re.escape(phrase).replace("\\ ", " ")


def title_filters(answers, role_titles):
    """Questions 7 and 8 as the two regex lists Settings holds. A search phrase naming a
    level word is repeated with each level word of the band the years in question 2 put the
    user in, because boards spell the same job at a different level; past the entry band the
    entry-level words are excluded outright."""
    band = next(name for floor, name in BANDS if _years(answers.get("experience")) >= floor)
    known = {word for words in role_titles.values() for word in words}
    filters = []
    for phrase in _phrases(answers.get("search_titles")):
        filters.append(phrase)
        words = phrase.split()
        for index, word in enumerate(words):
            if word.lower() in known:
                filters.extend(" ".join(words[:index] + [level] + words[index + 1:])
                               for level in role_titles[band])
    excludes = _phrases(answers.get("exclude_titles")) + ([] if band == "entry" else role_titles["entry"])
    return ([_pattern(p) for p in dict.fromkeys(filters)],
            [_pattern(p) for p in dict.fromkeys(excludes)])


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
    row and an optional resume as `(filename, bytes)`. The form accepts PDF and Word only,
    and `generate` raises SystemExit when every model attempt fails."""
    pdf, resume_text = None, ""
    if resume:
        filename, data = resume
        if filename.lower().endswith(".pdf"):
            pdf = data
        else:
            resume_text = docx_text(data)
    prompt = build_prompt(DATA.joinpath("profile_prompt.md").read_text(), answers, resume_text)
    profile, _model, _usage = generate(prompt, models, api_key, retries, pdf=pdf)
    title_filter, title_exclude = title_filters(answers, json.loads(DATA.joinpath("role_titles.json").read_text()))
    return profile, {
        "title_filter": "\n".join(title_filter),
        "title_exclude": "\n".join(title_exclude),
        "lookback_days": LOOKBACK_DAYS,
        "max_picks": MAX_PICKS,
    }
