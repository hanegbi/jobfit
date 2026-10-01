"""City fallback at aggregate time: a job located only as "Israel" gets a
city from its own text, else the company's registered address, else
techmap - so the page never shows a bare country where a city is known."""

from jobfit import pipeline, scoring
from jobfit.scripts import update_jobs


def test_israel_alone_is_not_a_city():
    assert scoring.canonical_city("Israel") is None
    assert scoring.canonical_city("ישראל") is None
    assert scoring.canonical_city("Tel Aviv, Israel") == "Tel Aviv"
    assert scoring.canonical_city("Rishon LeZion, Israel") == "Rishon Lezion"


def test_job_own_city_wins():
    loc, city, remote = pipeline._infer_location_fields({"location": "Haifa, Israel"}, "תל אביב-יפו", "Herzliya")
    assert (loc, city, remote) == ("Haifa", "Haifa", False)


def test_israel_only_location_falls_back_to_title_then_description():
    loc, city, _ = pipeline._infer_location_fields({"location": "Israel", "title": "Backend Engineer (Ramat Gan)", "description": "join our Tel Aviv team"}, None, "Herzliya")
    assert (loc, city) == ("Ramat Gan", "Ramat Gan")
    loc, city, _ = pipeline._infer_location_fields({"location": "Israel", "title": "Backend Engineer", "description": "Our office is in Petah Tikva."}, None, "Herzliya")
    assert (loc, city) == ("Petah Tikva", "Petah Tikva")


def test_israel_only_location_falls_back_to_the_company_address_then_techmap():
    loc, city, remote = pipeline._infer_location_fields({"location": "Israel", "title": "QA Engineer"}, "תל אביב-יפו", "Herzliya")
    assert (loc, city, remote) == ("Herzliya", "Herzliya", False)
    loc, city, remote = pipeline._infer_location_fields({"location": "Israel", "title": "QA Engineer"}, "תל אביב-יפו", None)
    assert (loc, city, remote) == ("Tel Aviv", "Tel Aviv", False)
    loc, city, remote = pipeline._infer_location_fields({"location": "Israel", "title": "QA Engineer"}, None, None)
    assert (loc, city, remote) == ("Israel", None, False)


def test_remote_israel_keeps_the_remote_flag_and_takes_the_company_city():
    loc, city, remote = pipeline._infer_location_fields({"location": "Remote, Israel", "title": "SRE"}, None, "Tel Aviv-Yafo")
    assert (loc, city, remote) == ("Tel Aviv", "Tel Aviv", True)


def test_a_job_that_never_said_where_has_no_location():
    """It used to answer the literal string "NaN", a pandas artifact that
    reached the page and read as a broken field. NULL says the same thing and
    every reader already handles it."""
    assert pipeline._infer_location_fields({"title": "Engineer"}, None, None) == (None, None, False)
    assert pipeline._infer_location_fields({"title": "Remote Engineer"}, None, None) == ("Remote", None, True)


def test_address_book_is_keyed_by_normalized_company_name(tmp_path, monkeypatch):
    path = tmp_path / "company_addresses.json"
    path.write_text('{"Acme Ltd.": [{"city": "Herzliya", "street": "HaMenofim", "number": "8"}], "NoCity Inc": [{"street": "x"}]}', encoding="utf-8")
    monkeypatch.setattr(update_jobs, "COMPANY_ADDRESSES_PATH", path)
    cities = update_jobs.load_company_address_cities()
    from jobfit import connections
    assert cities == {connections.normalize_company("Acme Ltd."): "Herzliya"}


def test_a_job_located_abroad_keeps_its_country_and_gets_no_city():
    # The company-address fallback must not relabel a US/UK role as the
    # company's Israeli city - that put foreign jobs on an Israel-only page.
    loc, city, remote = pipeline._infer_location_fields({"location": "United States", "title": "Senior DevOps Engineer"}, None, "Tel Aviv")
    assert (loc, city, remote) == ("United States", None, False)
    loc, city, _ = pipeline._infer_location_fields({"location": "London, United Kingdom", "title": "Account Executive"}, None, "Herzliya")
    assert (loc, city) == ("London, United Kingdom", None)


def test_an_israeli_location_is_never_read_as_foreign():
    loc, city, _ = pipeline._infer_location_fields({"location": "Israel", "title": "QA Engineer"}, None, "Herzliya")
    assert (loc, city) == ("Herzliya", "Herzliya")


def test_remote_abroad_stays_remote():
    loc, city, remote = pipeline._infer_location_fields({"location": "Remote, US", "title": "SRE"}, None, "Tel Aviv")
    assert (city, remote) == (None, True)


# --- a foreign place in the title beats the company's address ---------------

def test_a_title_naming_a_foreign_city_does_not_inherit_the_company_address():
    """The guard existed for the location field, which career-page listings
    leave empty, so the place sat in the title instead and 49 US and UK jobs
    were served as Tel Aviv roles."""
    for title in ("Senior Account Manager, London",
                  "Customer Success Manager Dallas HQ",
                  "Enterprise Account Executive Dallas, TX"):
        location, city, _ = pipeline._infer_location_fields({"title": title}, None, "Tel Aviv")
        assert city is None, title
        assert "Tel Aviv" not in (location or ""), title


def test_the_foreign_place_is_named_rather_than_just_denied():
    location, city, _ = pipeline._infer_location_fields(
        {"title": "Enterprise Account Executive Dallas, TX"}, None, "Ramat Gan")
    assert location == "Dallas, TX" and city is None


def test_an_israeli_job_still_inherits_the_company_city():
    """The guard must not fire on an ordinary title, or every job loses its
    city."""
    location, city, _ = pipeline._infer_location_fields({"title": "Senior DevOps Engineer"}, None, "Tel Aviv")
    assert (location, city) == ("Tel Aviv", "Tel Aviv")
