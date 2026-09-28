"""Plan-file id for a company. Byte-for-byte the same rule as
update_jobs._snake_case (which names companies/<id>.json) so a company's
plan and its job file share a stem. Duplicated rather than imported
because update_jobs imports this package, not the other way round; Plan C
replaces both with the registry id."""

import re


def plan_id_for(company: str) -> str:
    s = re.sub(r"[^\w\s-]", "", (company or "").lower()).strip()
    s = re.sub(r"[\s-]+", "_", s)
    return s or "unnamed_company"
