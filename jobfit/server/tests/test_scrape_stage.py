from datetime import datetime, timedelta, timezone

from jobfit import config
from jobfit.scripts import update_jobs


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_skips_a_company_checked_within_the_ttl():
    recent = _iso(datetime.now(timezone.utc) - timedelta(hours=1))
    assert update_jobs._should_skip_company({"last_checked": recent}, force=False) is True


def test_does_not_skip_a_stale_company():
    stale = _iso(datetime.now(timezone.utc) - timedelta(hours=config.COMPANY_RECHECK_TTL_HOURS + 1))
    assert update_jobs._should_skip_company({"last_checked": stale}, force=False) is False


def test_force_never_skips_even_if_recent():
    recent = _iso(datetime.now(timezone.utc))
    assert update_jobs._should_skip_company({"last_checked": recent}, force=True) is False


def test_never_checked_company_is_not_skipped():
    assert update_jobs._should_skip_company({}, force=False) is False
    assert update_jobs._should_skip_company({"last_checked": None}, force=False) is False
