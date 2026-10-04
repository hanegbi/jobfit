"""Fetch a page as plain text, politely, and give up quietly when blocked."""

import re
import threading
import time
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from jobfit_agent.agent import config

MAX_BYTES = 2_000_000   # a page bigger than this is a download, not a page


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "noscript"]):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))


class PoliteFetcher:
    """One request at a time, one per domain per delay, and never an unbounded read.

    The research graph fans out to five topic nodes on separate threads and they
    share this object. requests.Session is not thread-safe, and the shared
    per-domain delay table only means anything if one thread touches it at a
    time, so the whole fetch is serialised. Fetching is I/O against a handful of
    pages; the wait that matters on this machine is the model, not this.
    """

    def __init__(self, session=None, *, min_delay=config.MIN_DOMAIN_DELAY_S, sleep=time.sleep,
                 clock=time.monotonic, timeout=10):
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0 (jobfit-agent; personal research)"})
        self.min_delay, self.sleep, self.clock, self.timeout = min_delay, sleep, clock, timeout
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def fetch_text(self, url: str) -> str | None:
        with self._lock:
            return self._fetch(url)

    def _fetch(self, url: str) -> str | None:
        host = urlparse(url).netloc.lower()
        if host in self._last:
            wait = self._last[host] + self.min_delay - self.clock()
            if wait > 0:
                self.sleep(wait)
        self._last[host] = self.clock()
        try:
            # A read timeout bounds each chunk, not the whole response: a server
            # that trickles bytes forever would hang the run, so cap the body too.
            response = self.session.get(url, timeout=self.timeout, stream=True)
            if response.status_code != 200 or "html" not in response.headers.get("content-type", "html"):
                response.close()
                return None
            body = b""
            for chunk in response.iter_content(64 * 1024):
                body += chunk
                if len(body) >= MAX_BYTES:
                    break
            response.close()
        except requests.RequestException:
            return None
        return html_to_text(body.decode(response.encoding or "utf-8", errors="replace")) or None
