"""listing_heuristics.py decides which links on an arbitrary career page look
like real job postings (vs nav/footer chrome) - shared by both the plain-HTTP
fetch and the Playwright fallback. Zero test coverage before this, despite
being the thing standing between a real job list and a pile of "About Us" /
"Privacy Policy" junk in the scraped data."""

from jobfit.listing_heuristics import drop_category_prefix_links, looks_like_job_title


# --- looks_like_job_title --------------------------------------------------

def test_accepts_a_real_job_title():
    assert looks_like_job_title("Senior Backend Engineer") is True


def test_rejects_an_exact_nav_denylist_phrase():
    assert looks_like_job_title("Learn More") is False
    assert looks_like_job_title("View All") is False


def test_rejects_text_shorter_than_8_characters():
    assert looks_like_job_title("QA") is False


def test_rejects_text_longer_than_120_characters():
    assert looks_like_job_title("x" * 130) is False


def test_rejects_an_email_address():
    assert looks_like_job_title("jobs@acme.com") is False


def test_rejects_text_with_no_real_letters():
    assert looks_like_job_title("12345678") is False


# --- drop_category_prefix_links --------------------------------------------

def test_drops_a_department_overview_link_that_is_a_prefix_of_a_real_posting():
    results = [
        ("Engineering", "https://acme.com/careers/engineering/all"),
        ("Backend Engineer", "https://acme.com/careers/engineering/123/backend-engineer/all"),
    ]

    filtered = drop_category_prefix_links(results)

    assert filtered == [("Backend Engineer", "https://acme.com/careers/engineering/123/backend-engineer/all")]


def test_keeps_unrelated_sibling_postings():
    results = [
        ("Backend Engineer", "https://acme.com/careers/eng/1/backend-engineer"),
        ("Frontend Engineer", "https://acme.com/careers/eng/2/frontend-engineer"),
    ]

    assert drop_category_prefix_links(results) == results


def test_keeps_a_single_link_unchanged():
    results = [("Backend Engineer", "https://acme.com/careers/eng/1/backend-engineer")]
    assert drop_category_prefix_links(results) == results


def test_handles_an_empty_list():
    assert drop_category_prefix_links([]) == []
