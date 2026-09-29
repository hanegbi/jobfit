"""SQLite storage.

Every SQL statement in jobfit lives in this package; callers use the
functions here and never write SQL of their own. That boundary is what
lets the scrape and scoring suites keep testing behaviour rather than a
database.
"""

from jobfit.store.db import connect, migrate, shared

__all__ = ["connect", "migrate", "shared"]
