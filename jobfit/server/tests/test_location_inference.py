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


def test_empty_location_behaves_as_before():
    assert pipeline._infer_location_fields({"title": "Engineer"}, None, None) == ("NaN", None, False)
    assert pipeline._infer_location_fields({"title": "Remote Engineer"}, None, None) == ("Remote", None, True)


def test_address_book_is_keyed_by_normalized_company_name(tmp_path, monkeypatch):
    path = tmp_path / "company_addresses.json"
    path.write_text('{"Acme Ltd.": [{"city": "Herzliya", "street": "HaMenofim", "number": "8"}], "NoCity Inc": [{"street": "x"}]}', encoding="utf-8")
    monkeypatch.setattr(update_jobs, "COMPANY_ADDRESSES_PATH", path)
    cities = update_jobs.load_company_address_cities()
    from jobfit import connections
    assert cities == {connections.normalize_company("Acme Ltd."): "Herzliya"}
