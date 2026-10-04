"""Fetch a page as plain text, politely, and give up quietly when blocked."""

import re
import time
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from jobfit_agent.agent import config


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "noscript"]):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))


class PoliteFetcher:
    def __init__(self, session=None, *, min_delay=config.MIN_DOMAIN_DELAY_S, sleep=time.sleep,
                 clock=time.monotonic, timeout=10):
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0 (jobfit-agent; personal research)"})
        self.min_delay, self.sleep, self.clock, self.timeout = min_delay, sleep, clock, timeout
        self._last: dict[str, float] = {}

    def fetch_text(self, url: str) -> str | None:
        host = urlparse(url).netloc.lower()
        if host in self._last:
            wait = self._last[host] + self.min_delay - self.clock()
            if wait > 0:
                self.sleep(wait)
        self._last[host] = self.clock()
        try:
            response = self.session.get(url, timeout=self.timeout)
        except requests.RequestException:
            return None
        if response.status_code != 200 or "html" not in response.headers.get("content-type", "html"):
            return None
        return html_to_text(response.text) or None
