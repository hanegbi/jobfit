"""departments.canonical_department folds 378 raw ATS strings into one small
English vocabulary. Every case here is a real value from the store."""

import pytest

from jobfit import departments
from jobfit.departments import canonical_department as canon


@pytest.mark.parametrize("raw", ["R&D", "RND", "RnD", "R & D", "R&D ", "Engineering", "Engineering "])
def test_the_five_spellings_of_engineering_become_one(raw):
    assert canon(raw) == departments.ENGINEERING


@pytest.mark.parametrize("raw,expected", [
    ("הנדסה ופיתוח", departments.ENGINEERING),
    ("הנדסאים/טכנאים", departments.ENGINEERING),
    ("ייצור", departments.MANUFACTURING),
    ("סטודנטים", departments.STUDENTS),
    ("ניהול", departments.GENERAL),
    ("תפקידי מטה", departments.GENERAL),
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
    ("Public Sector Field Sales", departments.SALES),
    ("Business Development", departments.SALES),
    ("GCO - Customer Success & Services", departments.CUSTOMER),
    ("Customer Experience ", departments.CUSTOMER),
    ("Product Management", departments.PRODUCT),
    ("People & Operations", departments.PEOPLE),
    ("Threat & AI Research", departments.SECURITY),
    ("Software Engineering", departments.ENGINEERING),
    ("Tech Development", departments.ENGINEERING),
    ("Data & BI", departments.DATA_AI),
    ("Legal, Compliance, & Internal Audit", departments.LEGAL),
    ("G&A", departments.GENERAL),
])
def test_real_values_fold_into_the_vocabulary(raw, expected):
    assert canon(raw) == expected


def test_a_more_specific_rule_wins_over_a_general_one():
    """'Product Security' is Security work, not Product; 'Sales Engineer' is
    Sales, not Engineering. Rule order is what decides this, so it is tested."""
    assert canon("Product Security") == departments.SECURITY
    assert canon("Sales Engineering") == departments.SALES


@pytest.mark.parametrize("raw", [
    "348-RAT", "347-RANOPS", "IC 3", "IQCC", "SPG", "Taptica", "Stablecoin", "אחר", "", None, "   ",
])
def test_internal_codes_and_product_names_are_not_departments(raw):
    assert canon(raw) is None


def test_a_code_wrapping_a_real_word_keeps_the_real_word():
    """'ISL - Meta - MEPMS - (Project Delivery)' is 78 jobs of noise around
    two words that do say what the work is. The noise goes, the signal stays."""
    assert canon("ISL - Meta - MEPMS - (Project Delivery)") == departments.OPERATIONS


def test_every_result_is_in_the_published_vocabulary():
    """Nothing may invent a seventeenth department; the front end lists these."""
    samples = ["R&D", "Sales", "ייצור", "Threat & AI Research", "Legal", "IT", "Design", "348-RAT"]
    results = {canon(s) for s in samples} - {None}
    assert results <= set(departments.CANONICAL)
