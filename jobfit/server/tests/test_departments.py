"""departments.canonical_department folds 378 raw ATS strings into one small
English vocabulary. Every case here is a real value from the store.

The rule being tested throughout: a department names a FUNCTION. A level, a
programme, a place or a catch-all is not a department, and gets None.
"""

import pytest

from jobfit import departments
from jobfit.departments import canonical_department as canon


@pytest.mark.parametrize("raw", ["R&D", "RND", "RnD", "R & D", "R&D ", "Engineering", "Engineering "])
def test_the_five_spellings_of_engineering_become_one(raw):
    assert canon(raw) == departments.SOFTWARE


@pytest.mark.parametrize("raw,expected", [
    ("הנדסה ופיתוח", departments.SOFTWARE),
    ("הנדסאים/טכנאים", departments.HARDWARE),
    ("ייצור", departments.MANUFACTURING),
])
def test_hebrew_departments_come_back_in_english(raw, expected):
    assert canon(raw) == expected


def test_no_canonical_department_contains_hebrew():
    """The whole point: the app shows these, and the app is English."""
    from jobfit.translation import contains_hebrew

    assert not any(contains_hebrew(name) for name in departments.CANONICAL)


@pytest.mark.parametrize("raw,expected", [
    ("EMEA Field Sales", departments.SALES),
    ("WW GTM Sales Other", departments.SALES),
    ("Business Development", departments.SALES),
    ("GCO - Customer Success & Services", departments.CUSTOMER),
    ("Customer Experience ", departments.CUSTOMER),
    ("Product Management", departments.PRODUCT),
    ("People & Operations", departments.HR),
    ("Threat & AI Research", departments.SECURITY),
    ("Software Engineering", departments.SOFTWARE),
    ("Data & BI", departments.DATA_AI),
    ("Legal, Compliance, & Internal Audit", departments.LEGAL),
])
def test_real_values_fold_into_the_vocabulary(raw, expected):
    assert canon(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("R&D - Infrastructure", departments.DEVOPS),
    ("Cloud Ops", departments.DEVOPS),
    ("DevOps", departments.DEVOPS),
    ("R&D - Embedded SW", departments.HARDWARE),
    ("Silicon Photonics", departments.HARDWARE),
    ("Mechanical Engineering", departments.HARDWARE),
    ("System Validation & Verification", departments.QA),
])
def test_engineering_splits_the_way_engineering_job_boards_do(raw, expected):
    """"Engineering" alone would be 40% of every job that has a department
    and answer nothing. Infra, hardware and QA are separate kinds of work."""
    assert canon(raw) == expected


def test_a_more_specific_rule_wins_over_a_general_one():
    """'Product Security' is Security work, not Product; 'Sales Engineering'
    is Sales, not Software; 'Cloud Ops' is infrastructure, not business
    operations. Rule order is what decides these, so it is tested."""
    assert canon("Product Security") == departments.SECURITY
    assert canon("Sales Engineering") == departments.SALES
    assert canon("Cloud Ops") == departments.DEVOPS


@pytest.mark.parametrize("raw", ["ניהול", "Management", "IC 3", "Territory Management"])
def test_a_level_is_not_a_department(raw):
    assert canon(raw) is None


@pytest.mark.parametrize("raw", ["סטודנטים", "Students", "Internship", "Freelancers", "Graduates"])
def test_a_programme_is_not_a_department(raw):
    """Being a student says what stage you are at, not what work you do."""
    assert canon(raw) is None


@pytest.mark.parametrize("raw", ["CEO Office", "Back Office", "Tel-Aviv Office"])
def test_a_place_is_not_a_department(raw):
    assert canon(raw) is None


@pytest.mark.parametrize("raw", ["G&A", "General & Administrative", "General", "תפקידי מטה", "אחר"])
def test_a_catch_all_for_everything_but_the_product_is_not_a_department(raw):
    """'תפקידי מטה' is literally "staff roles" - 71 jobs whose only shared
    property is not being the product. That is not something to filter by."""
    assert canon(raw) is None


@pytest.mark.parametrize("raw", ["348-RAT", "347-RANOPS", "IQCC", "SPG", "Taptica", "Stablecoin",
                                 "Insurance", "Gaming", "", None, "   "])
def test_internal_codes_industries_and_product_names_are_not_departments(raw):
    assert canon(raw) is None


def test_an_exclusion_is_anchored_so_it_cannot_eat_a_real_department():
    """The exclusion list matches whole values only. 'Sales Management' is
    Sales; an unanchored 'management' would have thrown it away."""
    assert canon("Sales Management") == departments.SALES
    assert canon("Product Management") == departments.PRODUCT
    assert canon("Engineering - Management") == departments.SOFTWARE


def test_a_code_wrapping_a_real_word_keeps_the_real_word():
    """'ISL - Meta - MEPMS - (Project Delivery)' is 78 jobs of noise around
    two words that do say what the work is. The noise goes, the signal stays."""
    assert canon("ISL - Meta - MEPMS - (Project Delivery)") == departments.OPERATIONS


@pytest.mark.parametrize("raw,expected", [
    ("Automation", departments.QA),
    ("Validation", departments.QA),
    ("System Validation & Verification", departments.QA),
])
def test_automation_and_validation_are_qa(raw, expected):
    assert canon(raw) == expected


def test_drug_validation_is_laboratory_work_not_software_qa():
    """The word is shared; the job is not. 'Drug Discovery and Validation'
    is a real value from the store and read as QA before this rule."""
    assert canon("Drug Discovery and Validation") == departments.DATA_AI


# --- department_for: the title fills the ATS's silence ------------------------

@pytest.mark.parametrize("title,expected", [
    ("QA Automation Engineer", departments.QA),
    ("Senior SDET", departments.QA),
    ("Test Engineer", departments.QA),
    ("Senior Backend Engineer", departments.SOFTWARE),
    ("DevOps Engineer", departments.DEVOPS),
    ("MLOps Engineer", departments.DEVOPS),
    ("Embedded Software Engineer", departments.HARDWARE),
    ("Product Manager", departments.PRODUCT),
    ("UX Designer", departments.DESIGN),
    ("Account Executive", departments.SALES),
    ("Recruiter", departments.HR),
])
def test_a_title_gives_a_department_when_the_ats_gave_none(title, expected):
    """127 open QA roles were spread across five departments, exactly one of
    them QA - because almost no ATS states one. The title does."""
    assert departments.department_for(title) == expected


def test_what_the_company_actually_said_beats_the_title():
    """A stated department is evidence; an inferred one is a guess. The guess
    only fills silence."""
    assert departments.department_for("QA Automation Engineer", "Sales") == departments.SALES


def test_a_title_naming_only_a_level_gives_no_department():
    """'Engineering Team Lead' says how senior, not which function."""
    assert departments.department_for("Engineering Team Lead") is None


def test_a_title_that_matches_nothing_gives_no_department():
    assert departments.department_for("Shift Supervisor") is None
    assert departments.department_for(None) is None


def test_every_result_is_in_the_published_vocabulary():
    """Nothing may invent an eighteenth department; the front end lists these."""
    samples = ["R&D", "Sales", "ייצור", "Threat & AI Research", "Legal", "IT", "Design",
               "348-RAT", "Cloud Ops", "Hardware", "QA", "Management"]
    assert {canon(s) for s in samples} - {None} <= set(departments.CANONICAL)
