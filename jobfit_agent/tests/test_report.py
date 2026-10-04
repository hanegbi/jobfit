import json
import re

from jobfit_agent.agent.report import render
from jobfit_agent.tests.sample import HOSTILE, sample_report


def test_report_is_self_contained_and_escapes_untrusted_markup():
    html = render.render_html(sample_report())
    assert "<img src=x" not in html and "<script>alert" not in html   # hostile text exists only escaped, inside JSON
    assert "innerHTML" not in html                                    # the page builds DOM with text nodes
    assert not re.search(r'(src|href)="https?://', html)              # no external assets; links built at runtime


def test_embedded_json_round_trips_to_the_report():
    report = sample_report()
    html = render.render_html(report)
    payload = re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S).group(1)
    assert json.loads(payload) == report
    assert HOSTILE in json.loads(payload)["companies"][0]["jobs"][0]["fit"]["gaps"]


def test_page_has_a_tab_per_company_plus_an_overview():
    html = render.render_html(sample_report())
    assert 'role="tablist"' in html and "Overview" in html


def test_write_report_writes_json_and_html(tmp_path):
    path = render.write_report(sample_report(), tmp_path / "run")
    assert path.name == "report.html" and (tmp_path / "run" / "report.json").exists()
