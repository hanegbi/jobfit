"""Extractor tests: fixed JD/CV snippets with expected structured output,
so a taxonomy/keyword-list change can never silently break parsing without
a test noticing."""

from jobfit.ats_scorer import cv_extractor, jd_extractor
from jobfit.ats_scorer.models import RequirementKind, Seniority

import datetime


# --- JD extractor -------------------------------------------------------

def test_jd_extractor_splits_must_have_and_nice_to_have_sections():
    jd = """Senior Backend Engineer

Requirements:
- 5+ years of experience with Python
- Required: Kubernetes experience

Nice to have:
- Familiarity with Go is a plus
"""
    result = jd_extractor.extract_job_requirements(jd)

    must_have_texts = [r.text for r in result.must_have]
    nice_to_have_texts = [r.text for r in result.nice_to_have]
    assert any("Python" in t for t in must_have_texts)
    assert any("Kubernetes" in t for t in must_have_texts)
    assert any("Go" in t for t in nice_to_have_texts)
    assert not any("Go" in t for t in must_have_texts)


def test_jd_extractor_hard_word_overrides_softener_in_must_have_section():
    jd = """Backend Engineer

Requirements:
- Familiarity with AWS is required
"""
    result = jd_extractor.extract_job_requirements(jd)
    assert any("AWS" in r.text for r in result.must_have)
    assert not any("AWS" in r.text for r in result.nice_to_have)


def test_jd_extractor_extracts_required_years_total():
    jd = """Data Engineer

Requirements:
- At least 4 years of experience
- SQL required
"""
    result = jd_extractor.extract_job_requirements(jd)
    assert result.required_years_total == 4


def test_jd_extractor_infers_seniority_from_title():
    result = jd_extractor.extract_job_requirements("Requirements:\n- Python required", title="Staff Software Engineer")
    assert result.seniority == Seniority.STAFF


def test_jd_extractor_infers_seniority_from_years_when_title_has_no_signal():
    jd = """Software Engineer

Requirements:
- 7+ years of experience with Java
"""
    result = jd_extractor.extract_job_requirements(jd)
    assert result.seniority == Seniority.SENIOR


def test_a_title_is_classified_by_its_head_noun_not_a_trailing_qualifier():
    """Real bug: a title names its role first and says what it works on
    afterwards, but classify counted hits and broke a tie on whichever
    family came first in role_families.json. "Product Manager - Connectors
    and AI Infrastructure" tied 1-1 between product and ml_infra and went
    to ml_infra, putting a product role at 86 in a backend engineer's top
    band. The earlier match is the head noun, so it wins the tie."""
    from jobfit.ats_scorer.taxonomy import load_role_families

    families = load_role_families()
    assert families.classify("Product Manager - Connectors and AI Infrastructure") == "product"
    assert families.classify("Director, Product Management - AI Infrastructure") == "product"
    assert families.classify("Senior Paid Acquisition Manager - AI Infrastructure & Growth") == "marketing"
    # The qualifier still wins when it IS the role.
    assert families.classify("Senior AI Infrastructure Engineer") == "ml_infra"
    assert families.classify("Senior Backend Engineer") == "backend"


def test_jd_extractor_infers_role_family_from_title_and_responsibilities():
    jd = """Marketing Manager

Responsibilities:
- Own content marketing and campaign management strategy

Requirements:
- SEO experience required
"""
    result = jd_extractor.extract_job_requirements(jd)
    assert result.role_family == "marketing"


def test_jd_extractor_never_invents_a_requirement_not_in_the_text():
    jd = """Backend Engineer

Requirements:
- Python required
"""
    result = jd_extractor.extract_job_requirements(jd)
    all_text = " ".join(r.text for r in result.must_have + result.nice_to_have)
    assert "kubernetes" not in all_text.lower()
    assert "aws" not in all_text.lower()


def test_jd_extractor_finds_sections_in_a_single_line_collapsed_description():
    """Real bug caught live in production: jobfit's own scraper
    (ats_fetchers.strip_html/_clean) collapses every job description to a
    single whitespace-joined line with zero newlines - the section headers
    still have to be found as inline "Requirements:"/"Nice to have:"
    markers within that flowing text, not by scanning physical lines
    (which never existed for any real scraped job, so must_have/
    nice_to_have were silently always empty before this fix)."""
    jd = (
        "Join our team building next-gen infrastructure. Responsibilities: "
        "Design and build backend services. Own production systems end to end. "
        "Requirements: 5+ years of experience with Python. Kubernetes experience required. "
        "Must have AWS experience. BSc degree required. "
        "Nice to have: PostgreSQL is a plus. GraphQL experience is a bonus."
    )
    result = jd_extractor.extract_job_requirements(jd, title="Senior Backend Engineer")

    must_have_texts = [r.text for r in result.must_have]
    nice_to_have_texts = [r.text for r in result.nice_to_have]
    assert any("Python" in t for t in must_have_texts)
    assert any("Kubernetes" in t for t in must_have_texts)
    assert any("AWS" in t for t in must_have_texts)
    assert any("PostgreSQL" in t for t in nice_to_have_texts)
    assert any("GraphQL" in t for t in nice_to_have_texts)
    assert not any("PostgreSQL" in t or "GraphQL" in t for t in must_have_texts)
    assert result.role_family == "backend"
    assert result.required_years_total == 5


def test_jd_extractor_classifies_degree_requirement():
    jd = """Data Scientist

Requirements:
- MSc degree required
- Python required
"""
    result = jd_extractor.extract_job_requirements(jd)
    kinds = [r.kind for r in result.must_have]
    assert RequirementKind.DEGREE in kinds


# --- CV extractor ---------------------------------------------------------

_NOW = datetime.date(2026, 1, 1)


def test_cv_extractor_parses_roles_with_dates_and_bullets():
    cv = """Jane Doe

Senior Backend Engineer
Acme Corp | 2020 - Present
- Designed and owned distributed systems in production
- Built REST APIs in Python

Backend Engineer
Foo Inc | 2017 - 2020
- Developed microservices in Python
"""
    profile = cv_extractor.extract_candidate_profile(cv, reference_date=_NOW)
    assert len(profile.roles) == 2
    assert profile.roles[0].title == "Senior Backend Engineer"
    assert profile.roles[0].company == "Acme Corp"
    assert profile.roles[0].start == "2020"
    assert profile.roles[0].end == "present"
    assert len(profile.roles[0].bullets) == 2


def test_a_wrapped_bullet_is_one_bullet_not_two():
    """Real bug: a CV arrives one physical line at a time, so every wrapped
    bullet counted twice - the leading verb in the first half, and a second
    half that scored as a bullet with no evidence in it. That halved the
    bullet fraction scorer._evidence_depth_score measures, which is a flat
    deduction on every job the CV is ever scored against."""
    cv = """Jane Doe

Senior Backend Engineer
Acme Corp | 2020 - Present
- Designed and owned distributed systems in production, refactoring legacy
components to cut the error budget in half
- Built REST APIs in Python
"""
    profile = cv_extractor.extract_candidate_profile(cv, reference_date=_NOW)
    bullets = profile.roles[0].bullets
    assert len(bullets) == 2
    assert bullets[0].endswith("cut the error budget in half")


def test_a_cv_without_bullet_glyphs_still_reads_a_line_at_a_time():
    """The guard on the wrapped-bullet join: joining keys off a bullet
    glyph, so a CV that never uses one must keep the old line-per-bullet
    reading rather than collapsing its whole role into a single bullet."""
    cv = """Jane Doe

Senior Backend Engineer
Acme Corp | 2020 - Present
Designed and owned distributed systems in production
Built REST APIs in Python
"""
    profile = cv_extractor.extract_candidate_profile(cv, reference_date=_NOW)
    assert len(profile.roles[0].bullets) == 2


def test_the_sections_after_experience_are_not_the_last_roles_bullets():
    """Real bug: the last role's span runs to the end of the file, so SKILLS,
    EDUCATION and LANGUAGES all became its bullets - "Hebrew - Native" read
    as work evidence, and the dead entries diluted the bullet fraction."""
    cv = """Jane Doe

EXPERIENCE

Senior Backend Engineer
Acme Corp | 2020 - Present
- Designed and owned distributed systems in production

SKILLS

Programming Languages: Python, Go, Bash
Frameworks: Flask, FastAPI

LANGUAGES

Hebrew - Native | English - Fluent
"""
    profile = cv_extractor.extract_candidate_profile(cv, reference_date=_NOW)
    bullets = profile.roles[0].bullets
    assert len(bullets) == 1
    assert not any("Hebrew" in b or "Programming Languages" in b for b in bullets)


def test_cv_extractor_parses_roles_with_numeric_mm_yyyy_dates():
    """Real bug caught live in production: DATE_RANGE_RE only recognized a
    month NAME (Jan/January/...) or a bare year before the separator, never
    a numeric MM/YYYY date - the real CV this whole pipeline scores against
    uses exactly that format ("04/2022 - 07/2026") for every role except
    its oldest, bare-year one, so 4 of 5 real roles were invisible to
    _parse_roles and the candidate profile silently looked like someone
    with almost no work history at all."""
    cv = """Jane Doe

Senior Backend Engineer
Acme Corp | 04/2022 - 07/2026
- Designed and owned distributed systems in production

Backend Engineer
Foo Inc | 08/2020 - 03/2022
- Developed microservices in Python
"""
    profile = cv_extractor.extract_candidate_profile(cv, reference_date=_NOW)
    assert len(profile.roles) == 2
    assert profile.roles[0].start == "2022"
    assert profile.roles[0].end == "2026"
    assert profile.roles[1].start == "2020"
    assert profile.roles[1].end == "2022"


def test_cv_extractor_gives_strong_evidence_for_a_role_bullet_skill():
    cv = """Senior Backend Engineer
Acme Corp | 2023 - Present
- Built services in Python and Kubernetes
"""
    profile = cv_extractor.extract_candidate_profile(cv, reference_date=_NOW)
    python_skill = next(s for s in profile.skills if s.canonical == "Python")
    assert python_skill.evidence_strength == "strong"
    assert python_skill.recency_years == 0


def test_cv_extractor_gives_weak_evidence_for_a_skills_section_only_skill():
    cv = """Senior Backend Engineer
Acme Corp | 2023 - Present
- Built services

Skills: Python, Rust, Golang
"""
    profile = cv_extractor.extract_candidate_profile(cv, reference_date=_NOW)
    go_skill = next(s for s in profile.skills if s.canonical == "Go")
    assert go_skill.evidence_strength == "weak"


def test_cv_extractor_infers_seniority_from_most_recent_title():
    cv = """Staff Engineer
Acme Corp | 2022 - Present
- Led platform architecture
"""
    profile = cv_extractor.extract_candidate_profile(cv, reference_date=_NOW)
    assert profile.seniority == Seniority.STAFF


def test_cv_extractor_extracts_education_and_languages():
    cv = """Jane Doe

Education: BSc Computer Science

Languages: Fluent in English, native Hebrew

Senior Engineer
Acme | 2020 - Present
- Built systems
"""
    profile = cv_extractor.extract_candidate_profile(cv, reference_date=_NOW)
    assert profile.education
    assert profile.languages


def test_cv_extractor_recent_role_family_uses_the_two_most_recent_roles():
    cv = """Marketing Manager
Acme Corp | 2023 - Present
- Ran content marketing and SEO campaigns

QA Engineer
OldCo | 2015 - 2018
- Wrote test automation suites
"""
    profile = cv_extractor.extract_candidate_profile(cv, reference_date=_NOW)
    family = cv_extractor.recent_role_family(profile)
    assert family == "marketing"


# --- the "Company on its own line, Title+Dates below it" layout -----------
# Real bug caught live: _infer_title_for_role assumed the OTHER common
# layout ("Title\nCompany Dates") and swapped title/company for this one,
# which is Dan Hanegbi's own CV's layout - every role came back with the
# company name as its title ("Hailo", "McAfee"), which matches no role
# family keyword and silently empties every family-dependent score
# (title_and_seniority_fit, experience_relevance, the role_family_mismatch
# gate) for the whole scoring run.

def test_company_line_then_title_and_dates_is_not_swapped():
    cv = """Dan Hanegbi

EXPERIENCE

Hailo
Software Engineer 04/2022 - 07/2026
- Built a Python framework for AI embedded systems
"""
    profile = cv_extractor.extract_candidate_profile(cv, reference_date=_NOW)
    assert len(profile.roles) == 1
    assert profile.roles[0].title == "Software Engineer"
    assert profile.roles[0].company == "Hailo"


def test_two_titles_under_one_company_both_come_out_right():
    """The exact shape that makes this layout genuinely different from
    "Title\nCompany": one company, two stacked title+date blocks, no
    repeated company line for the second one."""
    cv = """Cyber Security Company
Automation Developer 08/2020 - 03/2022
- Built a Python automation framework

Software Quality Engineer 10/2018 - 08/2020
- Developed and executed test plans
"""
    profile = cv_extractor.extract_candidate_profile(cv, reference_date=_NOW)
    assert len(profile.roles) == 2
    assert profile.roles[0].title == "Automation Developer"
    assert profile.roles[0].company == "Cyber Security Company"
    assert profile.roles[1].title == "Software Quality Engineer"


def test_the_original_title_then_company_layout_still_works():
    """The fix must not flip the OTHER common layout, which every existing
    fixture in this file already uses and which was already correct."""
    cv = """Senior Backend Engineer
Acme Corp 2020 - Present
- Owned backend systems in production
"""
    profile = cv_extractor.extract_candidate_profile(cv, reference_date=_NOW)
    assert profile.roles[0].title == "Senior Backend Engineer"
    assert profile.roles[0].company == "Acme Corp"


def test_an_ambiguous_pair_with_no_title_word_either_side_keeps_old_behavior():
    """Neither line names a job - genuinely ambiguous, and the fix must not
    invent confidence it doesn't have. Falls back to the original
    first-layout assumption rather than guessing differently."""
    cv = """Acme Holdings
Project Falcon 2020 - 2022
- Shipped the thing
"""
    profile = cv_extractor.extract_candidate_profile(cv, reference_date=_NOW)
    assert profile.roles[0].title == "Acme Holdings"
    assert profile.roles[0].company == "Project Falcon"


# --- a dated Education entry must never become a work role -----------------
# Real bug caught live: Dan's CV has "B.Sc. Computer Science ... 2018-2021"
# under an EDUCATION header, and _find_date_ranges has no concept of
# section - it became a 6th "role". Harmless by luck for a 10-year work
# history (the real roles already fill roles[:2]); not harmless for a new
# grad, where the degree IS the only thing in that window.

def test_a_dated_education_entry_is_not_parsed_as_a_role():
    cv = """Jane Doe

EXPERIENCE

Senior Engineer
Acme | 2020 - Present
- Built systems

EDUCATION

B.Sc. Computer Science, State University     2014 - 2018
"""
    profile = cv_extractor.extract_candidate_profile(cv, reference_date=_NOW)
    assert len(profile.roles) == 1
    assert profile.roles[0].title == "Senior Engineer"


def test_education_before_experience_is_still_excluded():
    """Section order must not matter - only which section a date falls in."""
    cv = """Jane Doe

EDUCATION

B.Sc. Computer Science, State University     2014 - 2018

EXPERIENCE

Senior Engineer
Acme | 2020 - Present
- Built systems
"""
    profile = cv_extractor.extract_candidate_profile(cv, reference_date=_NOW)
    assert len(profile.roles) == 1
    assert profile.roles[0].title == "Senior Engineer"


def test_a_cv_with_no_section_headers_at_all_still_parses():
    """The 'experience' default before any header is seen must not regress
    every existing fixture, none of which have section headers."""
    cv = """Senior Backend Engineer
Acme Corp | 2020 - Present
- Owned backend systems
"""
    profile = cv_extractor.extract_candidate_profile(cv, reference_date=_NOW)
    assert len(profile.roles) == 1
