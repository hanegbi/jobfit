from __future__ import annotations

import re
from abc import ABC, abstractmethod

from jobfit.scrape.errors import FetchFailed, PlanInvalid
from jobfit.scrape.models import JobPosting, PostingSource


def to_posting(item: dict, source: PostingSource) -> JobPosting | None:
    title = " ".join((item.get("title") or "").split())
    if not title:
        return None
    return JobPosting(
        title=title, url=item.get("url"), location=item.get("location"),
        description=item.get("description") or "", department=item.get("department"),
        employment_type=item.get("employment_type"), posted_at=item.get("posted_at"), source=source,
    )


class AtsClient(ABC):
    provider: str = ""
    patterns: tuple[re.Pattern, ...] = ()

    def __init__(self, session):
        self.session = session

    def match(self, url: str | None) -> str | None:
        if not url:
            return None
        for pattern in self.patterns:
            m = pattern.search(url)
            if m:
                return m.group(1)
        return None

    @abstractmethod
    def board_url(self, board: str) -> str: ...

    @abstractmethod
    def _fetch_raw(self, board: str, known_url: str | None) -> list[dict] | None: ...

    def fetch_board(self, board: str, known_url: str | None = None, source: PostingSource = "ats_api") -> list[JobPosting]:
        raw = self._fetch_raw(board, known_url)
        if raw is None:
            raise FetchFailed(f"{self.provider} board {board!r}: no response")
        postings = [to_posting(item, source) for item in raw]
        return [p for p in postings if p is not None]


class AtsRegistry:
    def __init__(self, clients: list[AtsClient]):
        self._clients = clients
        self._by_provider = {c.provider: c for c in clients}

    def resolve(self, url: str | None) -> tuple[AtsClient, str] | None:
        for client in self._clients:
            board = client.match(url)
            if board:
                return client, board
        return None

    def client(self, provider: str) -> AtsClient:
        try:
            return self._by_provider[provider]
        except KeyError:
            raise PlanInvalid(f"unknown ATS provider {provider!r}") from None

    def providers(self) -> list[str]:
        return [c.provider for c in self._clients]
