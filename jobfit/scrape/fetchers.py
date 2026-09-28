"""Fetching a page as a `Page` model: plain HTTP, a headless browser, and a
TTL cache decorator over either. The extractor and enricher never know
which renderer produced the page they are given."""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import requests
from bs4 import BeautifulSoup

from jobfit import ats_fetchers
from jobfit.atomic_io import write_json_atomic
from jobfit.scrape.errors import FetchFailed
from jobfit.scrape.models import Page, Renderer

_JS_SHELL_MARKERS = ('id="root"', 'id="app"', 'id="__next"', "__NEXT_DATA__", 'id="___gatsby"', "data-reactroot")
JS_SHELL_MAX_TEXT = 500


def visible_text(html: str) -> str:
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:  # noqa: BLE001 - malformed markup must not break a fetch
        return ""
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    return " ".join(soup.get_text(" ").split())


def looks_like_js_shell(html: str, text: str) -> bool:
    return len(text) < JS_SHELL_MAX_TEXT and any(marker in html for marker in _JS_SHELL_MARKERS)


def make_page(requested_url: str, final_url: str, status: int, html: str, renderer: Renderer, now: datetime | None = None) -> Page:
    text = visible_text(html)
    return Page(
        url=final_url or requested_url, requested_url=requested_url, status=status, html=html, text=text,
        renderer=renderer, fetched_at=now or datetime.now(timezone.utc), is_js_shell=looks_like_js_shell(html, text),
    )


class PageFetcher(ABC):
    @abstractmethod
    def fetch(self, url: str) -> Page:
        """Return a Page for any real HTTP response (the caller inspects
        `status`); raise FetchFailed when no response could be obtained."""


class HttpPageFetcher(PageFetcher):
    def __init__(self, session: requests.Session, timeout: float = ats_fetchers.TIMEOUT):
        self.session = session
        self.timeout = timeout

    def fetch(self, url: str) -> Page:
        try:
            response = self.session.request("GET", url, timeout=self.timeout, allow_redirects=True)
        except requests.RequestException as error:
            raise FetchFailed(f"GET {url}: {error}") from error
        return make_page(url, getattr(response, "url", url), response.status_code, response.text or "", "http")


class PlaywrightPageFetcher(PageFetcher):
    """Headless Chromium render. Runs its own event loop on a dedicated
    thread so it works whether the caller is plain sync code or already
    inside a running asyncio loop (update_jobs' worker threads are the
    former today; the old cascade was the latter)."""

    def __init__(self, user_agent: str = ats_fetchers.USER_AGENT, nav_timeout_ms: int = 15000, settle_ms: int = 2000):
        self.user_agent = user_agent
        self.nav_timeout_ms = nav_timeout_ms
        self.settle_ms = settle_ms

    async def _fetch_async(self, url: str) -> Page:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(user_agent=self.user_agent)
            page = await context.new_page()
            try:
                response = await page.goto(url, timeout=self.nav_timeout_ms, wait_until="domcontentloaded")
                await page.wait_for_timeout(self.settle_ms)
                html = await page.content()
                final_url = page.url
                status = response.status if response is not None else 200
            finally:
                await browser.close()
        preview = visible_text(html)[:200].lower()
        if "access denied" in preview or "captcha" in preview:
            raise FetchFailed(f"{url}: blocked (WAF/captcha)")
        return make_page(url, final_url, status, html, "playwright")

    def fetch(self, url: str) -> Page:
        result: dict = {}

        def runner():
            try:
                result["page"] = asyncio.run(self._fetch_async(url))
            except Exception as error:  # noqa: BLE001 - surfaced below as FetchFailed
                result["error"] = error

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        thread.join()
        if "error" in result:
            error = result["error"]
            if isinstance(error, FetchFailed):
                raise error
            raise FetchFailed(f"playwright {url}: {error}") from error
        return result["page"]


class CachedPageFetcher(PageFetcher):
    """Decorator: serves a page from cache_dir when its entry is younger
    than ttl_hours, otherwise fetches through `inner` and stores the
    result. Failures are never cached."""

    def __init__(self, inner: PageFetcher, cache_dir: Path, ttl_hours: float, now: Callable[[], datetime] | None = None):
        self.inner = inner
        self.cache_dir = cache_dir
        self.ttl = timedelta(hours=ttl_hours)
        self.now = now or (lambda: datetime.now(timezone.utc))

    def _path(self, url: str) -> Path:
        return self.cache_dir / f"{hashlib.sha1(url.encode('utf-8')).hexdigest()}.json"

    def fetch(self, url: str) -> Page:
        path = self._path(url)
        if path.exists():
            try:
                entry = json.loads(path.read_text(encoding="utf-8"))
                fetched_at = datetime.fromisoformat(entry["fetched_at"])
                if fetched_at.tzinfo is None:
                    fetched_at = fetched_at.replace(tzinfo=timezone.utc)
                if self.now() - fetched_at < self.ttl:
                    return Page.model_validate(entry["page"])
            except (OSError, ValueError, KeyError):
                pass
        page = self.inner.fetch(url)
        write_json_atomic(path, {"fetched_at": page.fetched_at.isoformat(), "page": page.model_dump(mode="json")})
        return page


class PageFetcherFactory:
    def __init__(self, session: requests.Session, playwright_available: bool = True):
        self.session = session
        self.playwright_available = playwright_available

    def build(self, renderer: Renderer) -> PageFetcher:
        if renderer == "playwright" and self.playwright_available:
            return PlaywrightPageFetcher()
        return HttpPageFetcher(self.session)
