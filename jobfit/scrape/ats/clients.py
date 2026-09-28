"""One AtsClient per provider. Each owns its URL patterns (formerly
ats_fetchers.TOKEN_PATTERNS) and wraps the existing raw fetcher."""

from __future__ import annotations

import re

from jobfit import ats_fetchers
from jobfit.scrape.ats.base import AtsClient


class GreenhouseClient(AtsClient):
    provider = "greenhouse"
    patterns = (
        re.compile(r"greenhouse\.io/embed/job_board\?for=([A-Za-z0-9_-]+)", re.I),
        re.compile(r"boards(?:-api)?\.greenhouse\.io/(?:v1/boards/)?([A-Za-z0-9_-]+)", re.I),
    )

    def board_url(self, board: str) -> str:
        return f"https://boards.greenhouse.io/{board}"

    def _fetch_raw(self, board, known_url):
        return ats_fetchers.fetch_greenhouse(self.session, board)


class LeverClient(AtsClient):
    provider = "lever"
    patterns = (re.compile(r"(?:jobs|api)\.lever\.co/(?:v0/postings/)?([A-Za-z0-9_-]+)", re.I),)

    def board_url(self, board: str) -> str:
        return f"https://jobs.lever.co/{board}"

    def _fetch_raw(self, board, known_url):
        return ats_fetchers.fetch_lever(self.session, board)


class AshbyClient(AtsClient):
    provider = "ashby"
    patterns = (re.compile(r"(?:jobs|api)\.ashbyhq\.com/(?:posting-api/job-board/)?([A-Za-z0-9_-]+)", re.I),)

    def board_url(self, board: str) -> str:
        return f"https://jobs.ashbyhq.com/{board}"

    def _fetch_raw(self, board, known_url):
        return ats_fetchers.fetch_ashby(self.session, board)


class WorkableClient(AtsClient):
    provider = "workable"
    patterns = (
        re.compile(r"apply\.workable\.com/([A-Za-z0-9_-]+)", re.I),
        re.compile(r"https?://([A-Za-z0-9_-]+)\.workable\.com", re.I),
    )

    def board_url(self, board: str) -> str:
        return f"https://apply.workable.com/{board}/"

    def _fetch_raw(self, board, known_url):
        return ats_fetchers.fetch_workable(self.session, board)


class ComeetClient(AtsClient):
    provider = "comeet"
    patterns = (re.compile(r"comeet\.com/jobs(?:-api/[0-9.]+/company)?/([A-Za-z0-9_-]+)", re.I),)

    def board_url(self, board: str) -> str:
        return f"https://www.comeet.com/jobs/{board}"

    def _fetch_raw(self, board, known_url):
        jobs = ats_fetchers.fetch_comeet(self.session, board)
        if not jobs and known_url:
            hosted = ats_fetchers.comeet_board_url(known_url) or known_url
            jobs = ats_fetchers.fetch_comeet_hosted_page(self.session, hosted)
        return jobs
