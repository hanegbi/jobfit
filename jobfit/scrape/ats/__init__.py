"""Direct-API clients for the ATS providers jobfit knows, and the registry
that resolves a URL to (client, board). Adding a provider = one subclass
in clients.py + one entry in default_registry()."""

from jobfit.scrape.ats.base import AtsClient, AtsRegistry, to_posting
from jobfit.scrape.ats.clients import (
    AshbyClient, BambooHRClient, BreezyClient, ComeetClient, GreenhouseClient, LeverClient, PersonioClient,
    RecruiteeClient, SmartRecruitersClient, WorkableClient, WorkdayClient,
)


def default_registry(session) -> AtsRegistry:
    return AtsRegistry([
        GreenhouseClient(session), LeverClient(session), AshbyClient(session), WorkableClient(session), ComeetClient(session),
        RecruiteeClient(session), BambooHRClient(session), BreezyClient(session), SmartRecruitersClient(session),
        PersonioClient(session), WorkdayClient(session),
    ])


__all__ = ["AtsClient", "AtsRegistry", "to_posting", "default_registry",
           "GreenhouseClient", "LeverClient", "AshbyClient", "WorkableClient", "ComeetClient",
           "RecruiteeClient", "BambooHRClient", "BreezyClient", "SmartRecruitersClient", "PersonioClient", "WorkdayClient"]
