import pytest

from jobfit_agent.agent import models
from jobfit_agent.agent.schemas import Critique, FitAnalysis
from jobfit_agent.agent.testing import FakeLLM


def test_parse_model_spec_keeps_the_ollama_tag():
    assert models.parse_model_spec("ollama:qwen3:4b") == ("ollama", "qwen3:4b")
    assert models.parse_model_spec("anthropic:claude-haiku-4-5") == ("anthropic", "claude-haiku-4-5")


def test_unknown_provider_is_rejected(monkeypatch):
    monkeypatch.setitem(models.config.NODE_MODELS, "critic", "openai:gpt")
    with pytest.raises(ValueError, match="provider"):
        models.get_llm("critic")


class _Raw:
    usage_metadata = {"input_tokens": 12, "output_tokens": 5}


class _Chat:
    def __init__(self, outputs):
        self.outputs = list(outputs)

    def with_structured_output(self, schema, include_raw=True):
        return self

    def invoke(self, messages):
        return self.outputs.pop(0)


def _fit():
    return FitAnalysis(verdict="possible", strengths=["python"], gaps=[], deal_breakers=[],
                       score_agreement="agrees", rationale="ok")


def test_chat_llm_retries_once_on_a_parse_failure_and_reports_usage():
    chat = _Chat([{"parsed": None, "parsing_error": ValueError("bad"), "raw": _Raw()},
                  {"parsed": _fit(), "parsing_error": None, "raw": _Raw()}])
    obj, usage = models.ChatLLM(chat, "ollama:x").run(FitAnalysis, "sys", "user")
    assert obj.verdict == "possible"
    assert (usage.input_tokens, usage.output_tokens, usage.model) == (12, 5, "ollama:x")


def test_chat_llm_gives_up_after_two_parse_failures():
    bad = {"parsed": None, "parsing_error": ValueError("bad"), "raw": _Raw()}
    with pytest.raises(models.LLMParseError):
        models.ChatLLM(_Chat([bad, bad]), "ollama:x").run(FitAnalysis, "sys", "user")


def test_fake_llm_consumes_a_list_and_repeats_the_last_item():
    weak = Critique(grounded=False, addresses_gaps=False, fabricated_claims=[], feedback="more")
    good = Critique(grounded=True, addresses_gaps=True, fabricated_claims=[], feedback="")
    fake = FakeLLM({"Critique": [weak, good]})
    seen = [fake.run(Critique, "s", "u")[0] for _ in range(3)]
    assert [c.grounded for c in seen] == [False, True, True]
    assert fake.calls[0][0] == "Critique"


def test_cost_entry_shape():
    usage = models.Usage(input_tokens=3, output_tokens=4, seconds=1.5, model="fake")
    assert models.cost_entry("critic", usage) == {
        "node": "critic", "model": "fake", "input_tokens": 3, "output_tokens": 4, "seconds": 1.5}
