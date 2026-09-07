# Signup questionnaire

The form a user fills in once at signup. This is the specification: the
questions, the shape of each answer, and what each answer feeds. The web
form renders it; the Answers tab stores it one column per question; the
profile generator reads it alongside the resume.

Design rules:

- Field-agnostic. Domain specifics come from the resume and the two free
  text boxes, not from preset lists. Every list says "add your own" and
  means it.
- Realism questions ask for facts, not self-assessment, wherever a fact
  exists: years, titles, what was used at a job versus in a class.
- Answer shapes are things a phone can type: pick from a list and reorder,
  a number, a word from a fixed set, one sentence. Only questions 1, 2, 7,
  and 8 are required.
- Kept short. Twenty questions, about ten minutes.

Each question below is followed by *feeds:* the profile section or
pipeline setting it informs, and *shape:* the answer type.

## About you

1. **What field or industry are you in, or trying to get into?**
   *feeds:* profile summary; how every other answer is interpreted.
   *shape:* one line. Required.

2. **Years of relevant experience, and your most recent title.**
   *feeds:* the realism rubric (which years bands are easy, stretch, skip).
   *shape:* a number and a phrase. Required. Internships and certificates
   count; say so if that is what the number includes.

3. **Resume.**
   *feeds:* ground truth for experience, skills, education, certifications.
   *shape:* PDF or Word upload, optional, sent once to the model and not
   stored. The form says so under the upload.

4. **Tools and skills you would claim.** For each, mark *job* (used at a
   job), *class* (coursework or a project), or *heard of*.
   *feeds:* realism; the model treats "job" and "class" differently.
   *shape:* free list, one per line with the mark. No preset list, so the
   answer is not biased toward any field.

5. **Licenses or certifications you hold.** Star any you do not have but
   would get quickly if a job wanted it.
   *feeds:* realism; hard requirements in postings.
   *shape:* free list.

## The work

6. **Kinds of work that sound good.** Copy the list back in order from
   most to least appealing, delete what you would not want at all, add
   anything missing. The list is deliberately generic; add the specific
   version for your field.
   - analyzing data or information
   - building or making things
   - hands-on, field, or outdoor work
   - working directly with the public or customers
   - research and writing
   - coordinating projects or programs
   - teaching, training, or outreach
   - operating or maintaining systems or equipment
   *feeds:* profile lanes and their ranking.
   *shape:* reordered list.

7. **Titles you would search for.** Three to five phrases, the way a job
   board would spell them.
   *feeds:* the title filter that selects postings for you from the shared
   pool. Required; a user with no title filter is not run.
   *shape:* list of short phrases.

8. **Titles or kinds of role you never want to see**, even at a great
   place.
   *feeds:* the title exclude filter and the profile's dealbreakers.
   *shape:* list of short phrases. Required, may be "none".

9. **Employers you would work for.** Order by preference, delete any you
   would rule out, add your own.
   - government at any level
   - nonprofits
   - small companies and startups
   - large companies
   - consulting firms
   - universities and research institutions
   - contract or self-employed
   *feeds:* profile company-type preferences.
   *shape:* reordered list.

10. **A role below your training but inside your field, as a way in:**
    yes, maybe, or no.
    *feeds:* whether entry-level and adjacent titles are targets or
    exclusions.
    *shape:* one word.

## Hard lines

11. **Location.** Where you would work from, the longest one-way commute
    you would accept for a fully onsite job, whether hybrid is acceptable,
    and whether remote is required, preferred, or indifferent.
    *feeds:* profile must-haves; the model applies geography from prose.
    *shape:* a few phrases.

12. **Terms.** For each, reply *fine*, *meh*, or *no*.
    - weekends or evenings
    - on-call
    - shift rotation
    - seasonal
    - temporary or grant-funded with an end date
    - hourly pay
    - part-time
    - overnight travel
    - customer-facing
    *feeds:* profile penalties and dealbreakers.
    *shape:* one word per item.

13. **Physical conditions you would refuse.** Copy back any that apply,
    add your own, or say "all fine".
    - outdoors in bad weather
    - lifting 40 to 50 lb
    - standing or walking all day
    - long drives
    - heights, confined spaces, or water
    - chemicals or protective equipment
    - remote sites without cell service
    *feeds:* profile dealbreakers.
    *shape:* list or "all fine".

14. **Pay.** The lowest you would take and the number that would feel
    good, hourly or yearly. Which benefits would change the answer.
    *feeds:* profile compensation floor and the exception band.
    *shape:* two numbers and a phrase.

15. **Companies, industries, or employers you will not work for.**
    *feeds:* profile hard-no list.
    *shape:* list or "none".

## In your words

16. **About you.** Anything about your background the questions above did
    not give you room to say. Career-specific detail is the most useful
    thing here: what you are good at, what your resume gets wrong about
    you, what a hiring manager should understand.
    *feeds:* profile summary and the pitch the brief uses to explain a fit.
    *shape:* free text, a few sentences. Quoted into the profile rather
    than summarized.

17. **About what you want.** The job you would be happiest to have started
    six months from now, described as a normal workday. Plus anything the
    form could not ask.
    *feeds:* profile target roles and tone.
    *shape:* free text, a few sentences. Quoted rather than summarized.

## The brief

18. **Stretch roles**, a level or two above where you would likely get an
    interview: include them labeled as stretches, or leave them out.
    *feeds:* scoring rubric behavior at the edges.
    *shape:* one of two.

19. **Per listing, show:** why it fits you, what might disqualify you,
    salary when listed. Copy back the ones you want.
    *feeds:* brief format in the profile's writing section.
    *shape:* subset of three.

20. **Job boards or employers you already check.**
    *feeds:* the operator's queue of sources to add. Not a selector.
    *shape:* list or "none".

## Notes for the generator

- Resume is ground truth for experience and skills. Questions 1, 2, 4, and
  5 refine it; where they conflict, prefer the resume for facts and the
  answers for proficiency.
- Questions 16 and 17 are high signal. Quote them, do not paraphrase them
  away.
- Never copy contact details into the profile.
- Question 7 becomes the title filter and question 8 the exclude filter,
  after the operator's small mapping adds generic level words for the
  field (assistant, associate, specialist, technician, intern, senior, and
  so on) as appropriate to the years in question 2.
- Everything about frequency, pick count, and pause is a setting, not a
  question; defaults are daily, ten picks, active.
