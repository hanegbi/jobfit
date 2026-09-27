"""listing_heuristics.py decides which links on an arbitrary career page look
like real job postings (vs nav/footer chrome) - shared by both the plain-HTTP
fetch and the Playwright fallback. Zero test coverage before this, despite
being the thing standing between a real job list and a pile of "About Us" /
"Privacy Policy" junk in the scraped data."""

from jobfit.listing_heuristics import drop_category_prefix_links, looks_like_job_link_href, looks_like_job_title


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


def test_rejects_an_exact_white_papers_or_case_studies_nav_link():
    """Real false positive caught live: BugSec's careers page has a resources
    submenu ("White Papers", "Case Studies") sitting right next to the real
    job links, with link text that otherwise looks exactly like a job title."""
    assert looks_like_job_title("White Papers") is False
    assert looks_like_job_title("Case Studies") is False


def test_rejects_link_text_that_is_itself_a_url():
    """Real bug caught live: Simplex Mapping's career-page listing had an
    <a> whose visible text was its own href ("https://www.simplex-mapping.com/"),
    which passed every other check and got stored as a job title."""
    assert looks_like_job_title("https://www.simplex-mapping.com/") is False
    assert looks_like_job_title("www.example.com/careers") is False


# --- looks_like_job_link_href ------------------------------------------------

def test_looks_like_job_link_href_rejects_google_maps_links():
    """Real bug caught live: Artbrain's and BugSec's careers pages link their
    office addresses to Google Maps, and that anchor text ("HaMasger St 35,",
    "USA Office") passes looks_like_job_title just fine - only the link
    destination reveals it's not a job posting."""
    assert looks_like_job_link_href("https://www.google.com/maps/place/HaMasger+St+35") is False
    assert looks_like_job_link_href("https://maps.google.com/?q=Tel+Aviv") is False
    assert looks_like_job_link_href("https://goo.gl/maps/abc123") is False


def test_looks_like_job_link_href_accepts_a_normal_job_url():
    assert looks_like_job_link_href("https://acme.com/careers/backend-engineer") is True


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
