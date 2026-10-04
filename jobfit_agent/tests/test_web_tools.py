import requests

from jobfit_agent.agent.tools import fetch_page, web_search


def test_search_maps_backend_rows_and_swallows_errors():
    rows = [{"title": "Acme salaries", "href": "https://x.test/a", "body": "median $150k"}]
    hits = web_search.search("acme salary", backend=lambda q, n: rows)
    assert hits == [web_search.SearchHit("Acme salaries", "https://x.test/a", "median $150k")]

    def boom(q, n):
        raise RuntimeError("rate limited")
    assert web_search.search("acme", backend=boom) == []


def test_html_to_text_drops_scripts_and_collapses_whitespace():
    html = "<html><script>evil()</script><body><nav>menu</nav><p>Hello   <b>world</b></p></body></html>"
    assert fetch_page.html_to_text(html) == "Hello world"


class _Resp:
    def __init__(self, status=200, text="<p>hi</p>", ctype="text/html; charset=utf-8"):
        self.status_code, self.headers = status, {"content-type": ctype}
        self.encoding, self._body, self.closed = "utf-8", text.encode(), False

    def iter_content(self, size):
        for i in range(0, len(self._body), size):
            yield self._body[i:i + size]

    def close(self):
        self.closed = True


class _Session:
    def __init__(self, responses):
        self.responses, self.headers, self.urls = list(responses), {}, []

    def get(self, url, timeout, stream=False):
        self.urls.append(url)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_fetch_returns_text_and_none_on_blocks_and_errors():
    session = _Session([_Resp(), _Resp(status=403), requests.ConnectionError("down"), _Resp(ctype="application/pdf")])
    fetcher = fetch_page.PoliteFetcher(session, min_delay=0, sleep=lambda s: None)
    assert fetcher.fetch_text("https://a.test/1") == "hi"
    assert fetcher.fetch_text("https://a.test/2") is None
    assert fetcher.fetch_text("https://a.test/3") is None
    assert fetcher.fetch_text("https://a.test/4") is None


def test_fetcher_waits_between_requests_to_the_same_domain_only():
    now = [100.0]
    waits = []
    session = _Session([_Resp(), _Resp(), _Resp()])
    fetcher = fetch_page.PoliteFetcher(session, min_delay=1.5, sleep=waits.append, clock=lambda: now[0])
    fetcher.fetch_text("https://a.test/1")
    fetcher.fetch_text("https://a.test/2")      # same host, no time passed -> waits
    fetcher.fetch_text("https://b.test/1")      # other host -> no wait
    assert waits == [1.5]


def test_a_body_bigger_than_the_cap_is_truncated_not_streamed_forever():
    huge = _Resp(text="<p>" + "x" * (fetch_page.MAX_BYTES + 500_000) + "</p>")
    fetcher = fetch_page.PoliteFetcher(_Session([huge]), min_delay=0, sleep=lambda s: None)
    text = fetcher.fetch_text("https://a.test/big")
    # Reading stops at the first chunk that crosses the cap, so the body is bounded
    # by cap + one chunk — the point is that it stops, not that it lands exactly.
    assert text is not None and len(text) <= fetch_page.MAX_BYTES + 64 * 1024
    assert huge.closed


def test_fetching_is_serialised_so_threads_cannot_share_a_session():
    import threading
    session = _Session([_Resp(text="<p>a</p>"), _Resp(text="<p>b</p>"), _Resp(text="<p>c</p>")])
    fetcher = fetch_page.PoliteFetcher(session, min_delay=0, sleep=lambda s: None)
    out = []
    threads = [threading.Thread(target=lambda i=i: out.append(fetcher.fetch_text(f"https://a.test/{i}")))
               for i in range(3)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert sorted(out) == ["a", "b", "c"]
