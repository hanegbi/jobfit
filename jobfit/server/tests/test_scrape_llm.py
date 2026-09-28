"""LLMPlanClassifier against a stub LLMClient (never the network), and
the prompt it builds. AnthropicLLMClient is only constructed, never called."""

import json
import sys
from datetime import datetime, timezone

import pytest

from jobfit.scrape import classifiers, errors
from jobfit.scrape.candidates import CandidateExtractor
from jobfit.scrape.fetchers import make_page
from jobfit.scrape.models import Labels

NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)
CAREER = "https://acme.com/careers/"
HTML = "<nav><a href='/about'>About Us Page</a></nav><ul>" + "".join(
    f"<li><a href='/careers/job-{i}'>Engineer number {i}</a></li>" for i in range(5)
) + "</ul>"


class StubClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete_json(self, system, user, schema, max_tokens):
        self.calls.append({"system": system, "user": user, "schema": schema, "max_tokens": max_tokens})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _page_and_candidates():
    page = make_page(CAREER, CAREER, 200, HTML, "http", NOW)
    return page, CandidateExtractor().extract(page, CAREER)


def _good_labels(candidates):
    return {"page_verdict": "careers_page", "external_board_url": None, "container_selector": "ul",
            "candidates": [{"index": c.index, "is_job": c.href.startswith("https://acme.com/careers/"), "reason": "ok"} for c in candidates]}


def test_user_message_contains_url_truncated_text_and_the_candidate_table():
    page, candidates = _page_and_candidates()
    page = page.model_copy(update={"text": "x" * 5000})
    message = classifiers.build_user_message(page, candidates, CAREER, text_chars=3000)
    assert CAREER in message
    assert "x" * 3000 in message and "x" * 3001 not in message
    rows = json.loads(message[message.index("["):message.rindex("]") + 1])
    assert len(rows) == len(candidates)
    assert set(rows[0]) == {"index", "text", "href", "ancestor_path", "sibling_anchor_count", "in_chrome"}


def test_classifier_returns_validated_labels_and_sends_the_schema():
    page, candidates = _page_and_candidates()
    client = StubClient([_good_labels(candidates)])
    labels = classifiers.LLMPlanClassifier(client, model="claude-haiku-4-5").classify(page, candidates, CAREER)
    assert isinstance(labels, Labels) and sum(l.is_job for l in labels.candidates) == 5
    call = client.calls[0]
    assert call["schema"] is classifiers.LABELS_SCHEMA and call["max_tokens"] == 4096
    assert "job posting" in call["system"].lower()


def test_classifier_retries_once_on_invalid_output_then_succeeds():
    page, candidates = _page_and_candidates()
    client = StubClient([{"page_verdict": "maybe", "candidates": []}, _good_labels(candidates)])
    labels = classifiers.LLMPlanClassifier(client, model="m").classify(page, candidates, CAREER)
    assert len(client.calls) == 2
    assert "invalid" in client.calls[1]["user"].lower()
    assert labels.page_verdict == "careers_page"


def test_classifier_fails_after_two_invalid_answers_or_unknown_indexes():
    page, candidates = _page_and_candidates()
    bad = {"page_verdict": "careers_page", "candidates": [{"index": 999, "is_job": True, "reason": "?"}]}
    with pytest.raises(errors.ClassifierFailed):
        classifiers.LLMPlanClassifier(StubClient([bad, bad]), model="m").classify(page, candidates, CAREER)


def test_classifier_propagates_client_failures_as_classifier_failed():
    page, candidates = _page_and_candidates()
    with pytest.raises(errors.ClassifierFailed):
        classifiers.LLMPlanClassifier(StubClient([errors.ClassifierFailed("rate limited")]), model="m").classify(page, candidates, CAREER)


def test_classifiers_module_does_not_import_the_vendor_sdk():
    """Static check (the runtime guard is test_scrape_no_llm_at_runtime.py)."""
    import importlib.util
    source = importlib.util.find_spec("jobfit.scrape.classifiers").origin
    text = open(source, encoding="utf-8").read()
    assert "import anthropic" not in text and "llm_client" not in text


def test_anthropic_client_maps_a_text_response_to_json():
    anthropic = pytest.importorskip("anthropic")
    from jobfit.scrape.llm_client import AnthropicLLMClient

    client = AnthropicLLMClient(model="claude-haiku-4-5", api_key="test-key")

    class _Block:
        type = "text"
        text = json.dumps({"page_verdict": "careers_page", "candidates": []})

    class _Response:
        stop_reason = "end_turn"
        content = [_Block()]

    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return _Response()

    client.client.messages.create = fake_create
    data = client.complete_json("sys", "user", {"type": "object"}, 512)
    assert data == {"page_verdict": "careers_page", "candidates": []}
    assert captured["model"] == "claude-haiku-4-5" and captured["temperature"] == 0
    assert captured["output_config"] == {"format": {"type": "json_schema", "schema": {"type": "object"}}}
    assert captured["system"] == "sys" and captured["messages"] == [{"role": "user", "content": "user"}]


def test_anthropic_client_maps_sdk_errors_to_classifier_failed():
    anthropic = pytest.importorskip("anthropic")
    from jobfit.scrape.llm_client import AnthropicLLMClient

    client = AnthropicLLMClient(model="claude-haiku-4-5", api_key="test-key")

    def boom(**kwargs):
        raise anthropic.APIConnectionError(request=None)

    client.client.messages.create = boom
    with pytest.raises(errors.ClassifierFailed):
        client.complete_json("sys", "user", {"type": "object"}, 512)
