"""normalize_job_url - the identity form a job's id is hashed from
(update_jobs.compute_job_id), so a mistake here either merges two
different jobs into one id or mints a new id for the same posting on
every scrape."""

from jobfit.scrape.ids import normalize_job_url


def test_strips_whitespace_fragment_and_trailing_slash():
    assert normalize_job_url("  https://x.com/job/1/  ") == "https://x.com/job/1"
    assert normalize_job_url("https://x.com/job/1#apply") == "https://x.com/job/1"


def test_none_and_empty_are_none():
    assert normalize_job_url(None) is None
    assert normalize_job_url("") is None


def test_drops_a_cache_busting_timestamp_param():
    """Real bug caught live: tikalk.com appends "?t=<Date.now()>" to its own
    job links, a fresh value on every page load. Left in, every scrape
    mints a new id for the SAME posting - the old one closes, a duplicate
    reopens as "new", forever."""
    assert normalize_job_url("https://tikalk.com/career/senior-data-engineer-03.53c?t=1787123964592") == \
        "https://tikalk.com/career/senior-data-engineer-03.53c"
    # Different timestamp, same job: must normalize to the identical id.
    first = normalize_job_url("https://tikalk.com/career/x?t=111")
    second = normalize_job_url("https://tikalk.com/career/x?t=222")
    assert first == second


def test_drops_common_tracking_params_but_keeps_the_rest():
    url = "https://boards.greenhouse.io/acme/jobs/123?gh_src=abc&utm_source=linkedin&utm_campaign=x"
    # gh_src is a real ATS param (not in the volatile allowlist) and must survive.
    assert normalize_job_url(url) == "https://boards.greenhouse.io/acme/jobs/123?gh_src=abc"


def test_a_real_ats_job_id_query_param_survives():
    """The one thing this must never do: strip a query param that is the
    only thing distinguishing two different postings on the same path."""
    a = normalize_job_url("https://company.com/careers?jobId=111")
    b = normalize_job_url("https://company.com/careers?jobId=222")
    assert a != b


def test_a_url_with_no_query_string_is_unaffected():
    assert normalize_job_url("https://x.com/job/1") == "https://x.com/job/1"
