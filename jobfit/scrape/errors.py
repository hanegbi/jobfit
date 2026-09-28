"""Exceptions shared across the scrape package."""


class FetchFailed(RuntimeError):
    """A listing page, detail page or ATS board could not be fetched at all
    (network error, timeout, WAF block, 5xx). Propagates out of
    CompanyScrapeService.scrape so update_jobs._process_company records a
    failure and leaves the company file untouched - a transient outage must
    never close every stored job for a company."""


class PlanInvalid(ValueError):
    """A stored plan cannot be turned into a runnable strategy (e.g. an
    external_board URL no ATS client recognizes any more)."""


class ClassifierFailed(RuntimeError):
    """A PlanClassifier could not produce valid Labels (API error, schema
    validation failed twice, unknown candidate index). The planner falls
    back to RulesPlanClassifier when it sees this."""
