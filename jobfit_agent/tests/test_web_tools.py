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
        self.status_code, self.text, self.headers = status, text, {"content-type": ctype}


class _Session:
    def __init__(self, responses):
        self.responses, self.headers, self.urls = list(responses), {}, []

    def get(self, url, timeout):
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
