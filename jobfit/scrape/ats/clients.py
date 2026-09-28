"""One AtsClient per provider. Each owns its URL patterns (formerly
ats_fetchers.TOKEN_PATTERNS) and wraps the existing raw fetcher."""

from __future__ import annotations

import re

from jobfit import ats_fetchers
from jobfit.scrape.ats.base import AtsClient


class GreenhouseClient(AtsClient):
    provider = "greenhouse"
    patterns = (
        re.compile(r"greenhouse\.io/embed/job_board(?:/js)?\?for=([A-Za-z0-9_-]+)", re.I),
        re.compile(r"(?:boards|job-boards)(?:-api)?(?:\.eu)?\.greenhouse\.io/(?:v1/boards/)?(?!embed\b)([A-Za-z0-9_-]+)", re.I),
    )

    def board_url(self, board: str) -> str:
        return f"https://boards.greenhouse.io/{board}"

    def _fetch_raw(self, board, known_url):
        return ats_fetchers.fetch_greenhouse(self.session, board)


class LeverClient(AtsClient):
    provider = "lever"
    patterns = (re.compile(r"(?:jobs|api)\.lever\.co/(?:v0/postings/)?(?!v0\b)([A-Za-z0-9_-]+)", re.I),)

    def board_url(self, board: str) -> str:
        return f"https://jobs.lever.co/{board}"

    def _fetch_raw(self, board, known_url):
        return ats_fetchers.fetch_lever(self.session, board)


class AshbyClient(AtsClient):
    provider = "ashby"
    # The embed script lives at jobs.ashbyhq.com/ashby-job-board-embed.js - not a board.
    patterns = (re.compile(r"(?:jobs|api)\.ashbyhq\.com/(?:posting-api/job-board/)?(?!ashby-job-board-embed)([A-Za-z0-9_-]+)(?![\w-]*\.js)", re.I),)

    def board_url(self, board: str) -> str:
        return f"https://jobs.ashbyhq.com/{board}"

    def _fetch_raw(self, board, known_url):
        return ats_fetchers.fetch_ashby(self.session, board)


class WorkableClient(AtsClient):
    provider = "workable"
    patterns = (
        re.compile(r"apply\.workable\.com/(?!api\b|jobs\b)([A-Za-z0-9_-]+)", re.I),
        re.compile(r"https?://(?!www\.|apply\.|jobs\.|careers-page\.|help\.|resources\.)([A-Za-z0-9_-]+)\.workable\.com", re.I),
    )

    def board_url(self, board: str) -> str:
        return f"https://apply.workable.com/{board}/"

    def _fetch_raw(self, board, known_url):
        return ats_fetchers.fetch_workable(self.session, board)


class ComeetClient(AtsClient):
    """Two board forms: a comeet.com slug ("acme"), or an embedded-widget
    credential pair "UID:TOKEN" (e.g. "B3.006:3B61...") for companies that
    host Comeet's JS widget on their own careers page - see
    ats_fetchers.fetch_comeet_widget."""

    provider = "comeet"
    # Board = slug, or slug/ACCOUNT-UID when the URL carries it: the hosted
    # board page only renders its positions JSON at /jobs/<slug>/<uid>.
    patterns = (re.compile(r"comeet\.com/jobs(?:-api/[0-9.]+/company)?/([A-Za-z0-9_-]+(?:/[A-Z0-9]{2}\.[0-9A-F]{3})?)", re.I),)

    def match(self, url):
        if url:
            m = ats_fetchers.COMEET_WIDGET_API_RE.search(url)
            if m:
                return f"{m.group(1).upper()}:{m.group(2)}"
        return super().match(url)

    def board_url(self, board: str) -> str:
        if ":" in board:
            uid, token = board.split(":", 1)
            return f"https://www.comeet.co/careers-api/2.0/company/{uid}/positions?token={token}"
        return f"https://www.comeet.com/jobs/{board}"

    def _fetch_raw(self, board, known_url):
        if ":" in board:
            uid, token = board.split(":", 1)
            return ats_fetchers.fetch_comeet_widget(self.session, uid, token)
        slug = board.split("/", 1)[0]
        jobs = ats_fetchers.fetch_comeet(self.session, slug)
        if not jobs:
            hosted = (ats_fetchers.comeet_board_url(known_url) if known_url else None) or (self.board_url(board) if "/" in board else known_url)
            if hosted:
                jobs = ats_fetchers.fetch_comeet_hosted_page(self.session, hosted)
        return jobs


class RecruiteeClient(AtsClient):
    provider = "recruitee"
    patterns = (re.compile(r"https?://([a-z0-9-]+)\.recruitee\.com", re.I),)

    def board_url(self, board: str) -> str:
        return f"https://{board}.recruitee.com/"

    def _fetch_raw(self, board, known_url):
        return ats_fetchers.fetch_recruitee(self.session, board)


class BambooHRClient(AtsClient):
    provider = "bamboohr"
    patterns = (re.compile(r"https?://([a-z0-9-]+)\.bamboohr\.com/(?:careers|jobs)", re.I),)

    def board_url(self, board: str) -> str:
        return f"https://{board}.bamboohr.com/careers"

    def _fetch_raw(self, board, known_url):
        return ats_fetchers.fetch_bamboohr(self.session, board)


class BreezyClient(AtsClient):
    provider = "breezy"
    patterns = (re.compile(r"https?://([a-z0-9-]+)\.breezy\.hr", re.I),)

    def board_url(self, board: str) -> str:
        return f"https://{board}.breezy.hr/"

    def _fetch_raw(self, board, known_url):
        return ats_fetchers.fetch_breezy(self.session, board)


class SmartRecruitersClient(AtsClient):
    provider = "smartrecruiters"
    patterns = (
        re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_-]+)", re.I),
        re.compile(r"api\.smartrecruiters\.com/v1/companies/([A-Za-z0-9_-]+)", re.I),
    )

    def board_url(self, board: str) -> str:
        return f"https://jobs.smartrecruiters.com/{board}"

    def _fetch_raw(self, board, known_url):
        return ats_fetchers.fetch_smartrecruiters(self.session, board)


class PersonioClient(AtsClient):
    provider = "personio"
    patterns = (re.compile(r"https?://([a-z0-9-]+)\.jobs\.personio\.(?:de|com)", re.I),)

    def board_url(self, board: str) -> str:
        return f"https://{board}.jobs.personio.de/"

    def _fetch_raw(self, board, known_url):
        return ats_fetchers.fetch_personio(self.session, board)


class WorkdayClient(AtsClient):
    """board = "<tenant>.wd<n>/<site>", e.g. "motorolasolutions.wd5/Careers"."""

    provider = "workday"
    patterns = (re.compile(r"https?://([a-z0-9-]+\.wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?(?!wday\b|login\b)([A-Za-z0-9_-]+)", re.I),)

    def match(self, url):
        if not url:
            return None
        m = self.patterns[0].search(url)
        return f"{m.group(1).lower()}/{m.group(2)}" if m else None

    def board_url(self, board: str) -> str:
        host_part, _, site = board.partition("/")
        return f"https://{host_part}.myworkdayjobs.com/{site}"

    def _fetch_raw(self, board, known_url):
        return ats_fetchers.fetch_workday(self.session, board)
