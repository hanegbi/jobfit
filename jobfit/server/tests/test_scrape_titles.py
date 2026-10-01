"""A listing link's text is often a whole job card - title, department,
location, employment type, seniority, an "Apply" CTA - so the title has to
be split back out of it. Cases here are real card texts caught live."""

import pytest

from jobfit.scrape.titles import (
    CardText,
    authoritative_title,
    detail_title_candidates,
    names_foreign_country,
    names_foreign_place,
    split_card_text,
)


def test_strips_a_trailing_israeli_city():
    assert split_card_text("Senior Platform Engineer (DevEx / DevOps) Tel Aviv").title == (
        "Senior Platform Engineer (DevEx / DevOps)"
    )


def test_strips_a_trailing_employment_type_seniority_and_city_run():
    parsed = split_card_text("Senior MLOps Engineer Full-time Senior Tel Aviv")
    assert parsed.title == "Senior MLOps Engineer"
    assert parsed.location == "Tel Aviv"
    assert parsed.employment_type == "Full-time"


def test_strips_dot_separated_metadata_segments():
    parsed = split_card_text("Senior Software Engineer Tel Aviv, Israel · Full-time · Senior")
    assert parsed.title == "Senior Software Engineer"
    assert parsed.location == "Tel Aviv, Israel"


def test_strips_a_trailing_apply_cta():
    assert split_card_text("DevOps Engineer Tel Aviv, Israel Apply").title == "DevOps Engineer"
    assert split_card_text("Senior Backend Engineer Engineering Tel Aviv Apply Now").title == (
        "Senior Backend Engineer Engineering"
    )


def test_strips_a_location_label_and_its_value():
    parsed = split_card_text("Senior DevOps Engineer (FedRAMP) Location United States")
    assert parsed.title == "Senior DevOps Engineer (FedRAMP)"
    assert parsed.location == "United States"


def test_strips_a_leading_city_prefix():
    parsed = split_card_text("Tel Aviv, Israel Senior Low-level Software Engineer Full-time")
    assert parsed.title == "Senior Low-level Software Engineer"
    assert parsed.location == "Tel Aviv, Israel"


def test_keeps_a_title_that_is_only_a_title():
    for title in ("Senior Backend Engineer", "Engineering Manager", "Sales Associate",
                  "VP Research & Development", "מהנדס/ת תוכנה"):
        assert split_card_text(title).title == title


def test_keeps_location_and_mode_words_that_are_part_of_the_title():
    # A parenthesised/bracketed mode is how the title itself says "remote",
    # and a dash-introduced region is part of the role's name.
    for title in ("Sr. Vulnerability Researcher (Remote)", "Controller - Part-Time (50%)",
                  "Regional Sales Director USA [Remote]", "Account Executive - Germany (DACH)"):
        assert split_card_text(title).title == title


def test_a_card_that_is_only_metadata_yields_no_title():
    for text in ("Apply now", "Tel Aviv · Full-time", "Tel Aviv, Israel"):
        assert split_card_text(text).title == ""


def test_detail_title_candidates_reads_h1_og_title_and_jsonld():
    html = """<html><head><title>Senior MLOps Engineer - Noma Security</title>
    <meta property="og:title" content="Senior MLOps Engineer"></head>
    <body><h1>Senior MLOps Engineer</h1></body></html>"""
    assert "Senior MLOps Engineer" in detail_title_candidates(html)


def test_authoritative_title_only_ever_trims_the_listing_title():
    listing = "Senior MLOps Engineer Full-time Senior Tel Aviv"
    assert authoritative_title(["Senior MLOps Engineer", "Take your next step."], listing) == "Senior MLOps Engineer"
    # A page-wide heading that is not part of the listing title is ignored,
    # so a wrong <h1> can never replace a good title.
    assert authoritative_title(["Careers at Noma", "Join us"], listing) is None
    assert authoritative_title(["Senior MLOps Engineer Full-time Senior Tel Aviv"], listing) is None


def test_authoritative_title_ignores_a_too_short_fragment():
    assert authoritative_title(["Senior"], "Senior MLOps Engineer Full-time Senior Tel Aviv") is None


def test_an_israeli_city_with_a_country_code_is_not_foreign():
    assert names_foreign_country("Herzliya, IL") is False
    assert names_foreign_country("Tel Aviv, Israel") is False
    assert names_foreign_country("United States") is True
    assert names_foreign_country(None) is False


def test_a_work_mode_is_not_reported_as_a_location():
    """"Hybrid" as a location made the Israel relevance check drop the job."""
    assert split_card_text("DevOps Engineer Hybrid") == CardText(title="DevOps Engineer")
    assert split_card_text("DevOps Engineer On-site Tel Aviv").location == "Tel Aviv"
    # Remote is worth keeping: downstream reads it as the location it is.
    assert split_card_text("Backend Engineer Remote").location == "Remote"


def test_ordinary_title_words_survive_next_to_a_city():
    for text, title in (
        ("Sales Associate Tel Aviv", "Sales Associate"),
        ("Customer Success Associate Herzliya", "Customer Success Associate"),
        # "Management" stays: as a trailing word it is indistinguishable from
        # a real title ending, and a residual tag beats a damaged title.
        ("Data Platform Group Lead Management Full-time", "Data Platform Group Lead Management"),
        ("Engineering Manager Tel Aviv", "Engineering Manager"),
    ):
        assert split_card_text(text).title == title


def test_the_strip_does_not_depend_on_the_order_of_the_tags():
    for text in ("Software Engineer Tel Aviv Senior", "Software Engineer Senior Tel Aviv",
                 "Software Engineer Full-time Tel Aviv Mid"):
        assert split_card_text(text).title == "Software Engineer"


def test_a_department_first_card_is_titled_by_the_role_not_the_department():
    assert split_card_text("Engineering | Senior Backend Engineer | Tel Aviv").title == "Senior Backend Engineer"
    assert split_card_text("R&D · Senior Backend Engineer · Tel Aviv").title == "Senior Backend Engineer"


def test_israels_own_country_code_is_not_a_foreign_country():
    # ATS boards emit ", IL" on Israeli towns that are not in CITY_ALIASES.
    assert names_foreign_country("Tirat Carmel, IL") is False
    assert names_foreign_country("Sderot, IL") is False


def test_authoritative_title_never_drops_a_leading_word_that_is_not_metadata():
    # A page heading reading "Backend Engineer" must not demote the card's
    # "Senior Backend Engineer" to a different, more junior role.
    assert authoritative_title(["Backend Engineer"], "Senior Backend Engineer Tel Aviv") is None
    # A city the card leads with is metadata, so trimming it is fine.
    assert authoritative_title(
        ["Senior Low-level Software Engineer"], "Tel Aviv, Israel Senior Low-level Software Engineer Full-time"
    ) == "Senior Low-level Software Engineer"


# --- foreign places, not only Israeli ones ----------------------------------

@pytest.mark.parametrize("card,expected", [
    ("Senior DevOps Engineer Dallas HQ", "Senior DevOps Engineer"),
    ("Enterprise Account Executive Dallas, TX", "Enterprise Account Executive"),
    ("Senior Account Manager, London", "Senior Account Manager"),
    ("Solutions Engineer (Pre-Sales) Dallas, TX", "Solutions Engineer (Pre-Sales)"),
    ("Senior Software Engineer- Dallas Dallas HQ", "Senior Software Engineer"),
])
def test_a_trailing_foreign_place_is_card_metadata(card, expected):
    """Real cards from the store. The strip knew Israeli cities only, so a US
    listing kept its office in the title and a title-only search matched it."""
    assert split_card_text(card).title == expected


@pytest.mark.parametrize("card", [
    "Head of London Sales",
    "Dallas Account Lead",
    "Berlin Operations Manager",
    "Head of Delivery",
    "Director, Operations",
])
def test_a_place_inside_a_title_is_left_alone(card):
    """Only a TRAILING place is metadata; a role named after a region keeps
    its own words."""
    assert split_card_text(card).title == card


@pytest.mark.parametrize("text,foreign", [
    ("Senior Account Manager, London", True),
    ("Customer Success Manager Dallas HQ", True),
    ("QA Engineer, Bengaluru", True),
    ("Sales Director, Austin, TX", True),
    ("Senior DevOps Engineer", False),
    ("Senior Backend Engineer Tel Aviv", False),
    ("Engineer, Israel", False),
    ("Head of Mobile", False),
    ("Reading Comprehension Analyst", False),
    ("Senior Engineer, IL", False),
])
def test_names_foreign_place_reads_cities_as_well_as_countries(text, foreign):
    """names_foreign_country only reads a location field. Career-page listings
    leave that empty and put the place in the title."""
    assert names_foreign_place(text) is foreign
