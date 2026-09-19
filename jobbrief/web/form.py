"""The signup form: the lists the questionnaire offers back to the user, and one `Answers`
row from a submitted form.

The lists live here rather than in the template because the submit handler reads the
answers back against them, and the two must agree. `docs/questionnaire.md` is the
specification for both; its column table fixes the names and their order."""

from jobbrief.sheet import ANSWERS_HEADER


# Questions 6, 9, 12, 13 and 19. Each is offered back to the user to reorder, tick, or
# rate; every list says "add your own" and means it.
LISTS = {
    "work_types": [
        "analyzing data or information",
        "building or making things",
        "hands-on, field, or outdoor work",
        "working directly with the public or customers",
        "research and writing",
        "coordinating projects or programs",
        "teaching, training, or outreach",
        "operating or maintaining systems or equipment",
    ],
    "employer_types": [
        "government at any level",
        "nonprofits",
        "small companies and startups",
        "large companies",
        "consulting firms",
        "universities and research institutions",
        "contract or self-employed",
    ],
    "terms": [
        "weekends or evenings",
        "on-call",
        "shift rotation",
        "seasonal",
        "temporary or grant-funded with an end date",
        "hourly pay",
        "part-time",
        "overnight travel",
        "customer-facing",
    ],
    "physical": [
        "outdoors in bad weather",
        "lifting 40 to 50 lb",
        "standing or walking all day",
        "long drives",
        "heights, confined spaces, or water",
        "chemicals or protective equipment",
        "remote sites without cell service",
    ],
    "per_listing": ["why it fits you", "what might disqualify you", "salary when listed"],
}

TERM_ANSWERS = ["fine", "meh", "no"]


def blank_answers():
    """A first signup's starting point: every column empty but the lists the questionnaire
    offers back, which arrive already filled in for the user to reorder or untick."""
    answers = {column: "" for column in ANSWERS_HEADER}
    for column in ("work_types", "employer_types", "per_listing"):
        answers[column] = "\n".join(LISTS[column])
    return answers


def _lines(cell):
    return [line.strip() for line in (cell or "").splitlines() if line.strip()]


def controls(answers):
    """The tick marks and radio picks an `Answers` row sets, which a cell of joined phrases
    cannot express on its own. A retake and a first signup both render the form from this."""
    refused = _lines(answers.get("physical"))
    offered = LISTS["physical"]
    return {
        "terms": {term.strip(): answer.strip()
                  for term, answer in (line.split(":", 1) for line in _lines(answers.get("terms")) if ":" in line)},
        "physical": [item for item in refused if item in offered],
        # "all fine" is the empty answer, not something the user typed.
        "physical_other": ", ".join(item for item in refused if item not in offered and item != "all fine"),
        "per_listing": _lines(answers.get("per_listing")),
    }


def answers_from_form(form, resume_filename, submitted):
    """One `Answers` row keyed by the questionnaire's column names. A radio grid and a
    checkbox list both come out as one phrase per line, which is how the profile generator
    reads a list answer."""
    answers = {column: form.get(column, "").strip() for column in ANSWERS_HEADER}
    answers["resume"] = resume_filename
    answers["submitted"] = submitted
    answers["terms"] = "\n".join(f"{term}: {form[f'terms-{index}']}"
                                 for index, term in enumerate(LISTS["terms"]) if form.get(f"terms-{index}"))
    refused = [line for line in form.getlist("physical") + [form.get("physical_other", "").strip()] if line]
    answers["physical"] = "\n".join(refused) or "all fine"
    answers["per_listing"] = "\n".join(form.getlist("per_listing"))
    return answers
