"""Direct-API clients for the ATS providers jobfit knows, and the registry
that resolves a URL to (client, board). Adding a provider = one subclass
in clients.py + one entry in default_registry()."""

from jobfit.scrape.ats.base import AtsClient, AtsRegistry, to_posting
from jobfit.scrape.ats.clients import AshbyClient, ComeetClient, GreenhouseClient, LeverClient, WorkableClient


def default_registry(session) -> AtsRegistry:
    return AtsRegistry([
        GreenhouseClient(session), LeverClient(session), AshbyClient(session), WorkableClient(session), ComeetClient(session),
    ])


__all__ = ["AtsClient", "AtsRegistry", "to_posting", "default_registry",
           "GreenhouseClient", "LeverClient", "AshbyClient", "WorkableClient", "ComeetClient"]
