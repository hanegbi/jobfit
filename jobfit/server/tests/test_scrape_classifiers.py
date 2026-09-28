"""PlanClassifier implementations that never touch a model: the rules
classifier (the LLM-free fallback and the base the LLM refines) and the
recorded classifier (replays saved Labels in tests)."""

import json
from datetime import datetime, timezone

import pytest

from jobfit.scrape import classifiers, errors
from jobfit.scrape.candidates import CandidateExtractor
from jobfit.scrape.fetchers import make_page
from jobfit.scrape.models import CandidateLabel, Labels

CAREER = "https://acme.com/careers/"
HTML = """
<nav><a href="/about">About Us Page</a></nav>
<ul>
 <li><a href="/careers/backend-1">Backend Engineer</a></li>
 <li><a href="/careers/frontend-2">Frontend Engineer</a></li>
 <li><a href="/careers/devops-3">DevOps Engineer</a></li>
</ul>
<a href="/code-governance">Code Governance and Compliance</a>
"""


def _page(html=HTML, url=CAREER):
    return make_page(url, url, 200, html, "http", datetime(2026, 9, 28, tzinfo=timezone.utc))


def test_rules_chain_order():
    assert [f.name for f in classifiers.rules_chain().filters] == ["denylist", "href_marker", "category_prefix", "url_shape", "evidence"]


def test_rules_classifier_labels_every_candidate_in_index_order():
    page = _page()
    candidates = CandidateExtractor().extract(page, CAREER)
    labels = classifiers.RulesPlanClassifier().classify(page, candidates, CAREER)
    assert labels.page_verdict == "careers_page"
    assert [l.index for l in labels.candidates] == [c.index for c in candidates]
    by_text = {c.text: l for c, l in zip(candidates, labels.candidates)}
    assert by_text["Backend Engineer"].is_job is True
    assert by_text["About Us Page"].is_job is False
    assert by_text["Code Governance and Compliance"].is_job is False
    assert all(len(l.reason) <= 200 for l in labels.candidates)


def test_rules_classifier_reports_js_shell_pages():
    shell = '<html><body><div id="root"></div><script>window.__NEXT_DATA__={}</script></body></html>'
    page = _page(shell)
    labels = classifiers.RulesPlanClassifier().classify(page, [], CAREER)
    assert labels.page_verdict == "js_shell" and labels.candidates == []


def test_recorded_classifier_replays_by_career_url_and_fails_loudly_otherwise(tmp_path):
    recorded = Labels(page_verdict="careers_page", candidates=[CandidateLabel(index=0, is_job=True, reason="fixture")])
    (tmp_path / "acme.json").write_text(json.dumps({"career_url": CAREER, "labels": recorded.model_dump(mode="json")}), encoding="utf-8")
    classifier = classifiers.RecordedPlanClassifier.from_dir(tmp_path)
    assert classifier.classify(_page(), [], CAREER) == recorded
    with pytest.raises(errors.ClassifierFailed):
        classifier.classify(_page(), [], "https://other.com/jobs")
