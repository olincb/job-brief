"""Signup's model stage: the prose profile drafted from one user's questionnaire answers
and resume, and the title filters and condense vocabulary derived from the same answers.
It runs before anything exists in the user's Drive, so a model failure ends signup with
nothing to clean up."""

import io
import re
import zipfile
from importlib.resources import files
from xml.etree import ElementTree

from jobbrief.llm import generate
from jobbrief.sheet import ANSWERS_HEADER, LOOKBACK_DAYS_DEFAULT, MAX_PICKS_DEFAULT


DATA = files("jobbrief.data")

# The Answers columns the model is shown: the resume filename and the submit date say
# nothing about the person.
ASKED = [column for column in ANSWERS_HEADER if column not in ("resume", "submitted")]

WORD_XML = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# Question 4's job/class/heard-of mark and any aside after it ("python: job, mostly scripts");
# the mark is a whole word, so "JobRunner" stays.
TOOL_MARK = re.compile(r"[\s\W]+(?:job|class|heard of)\b.*$", re.IGNORECASE)


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
    """A typed phrase as one regex line of a Settings cell: escaped so `C++` is literal,
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
    neither a PDF nor a .docx, and `generate` raises ModelError when every model attempt
    fails; both leave signup with nothing written."""
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
    profile, _model, _usage = generate(prompt, models, api_key, retries, pdf=pdf)
    title_filter, title_exclude = title_filters(answers)
    # A starred license is one the user would get, so a posting's line naming it matters too.
    vocabulary = ([TOOL_MARK.sub("", phrase) for phrase in _phrases(answers.get("tools"))]
                  + [phrase.strip("* ") for phrase in _phrases(answers.get("certifications"))])
    return profile, {
        "title_filter": "\n".join(title_filter),
        "title_exclude": "\n".join(title_exclude),
        "vocabulary": "\n".join(vocabulary),
        "lookback_days": LOOKBACK_DAYS_DEFAULT,
        "max_picks": MAX_PICKS_DEFAULT,
    }
