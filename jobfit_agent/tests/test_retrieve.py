from jobfit_agent.agent import config, research
from jobfit_agent.agent.retrieve import top_chunks

# Pages long enough that truncation and retrieval genuinely differ: a real careers or
# review page is mostly chrome, with one paragraph that answers the question.
PAGES = {
    "https://a.test": "Cookies policy. " * 400
                      + "The median base salary for a backend engineer is 150000 dollars. "
                      + "Footer links. " * 400,
    "https://b.test": "We love dogs and hiking. " * 600,
}


def test_the_chunk_that_answers_the_query_is_selected():
    chosen = top_chunks(PAGES, "backend engineer salary", k=1, size=200)
    assert list(chosen) == ["https://a.test"] and "150000" in chosen["https://a.test"]
    assert len(chosen["https://a.test"]) <= 250


def test_empty_input_and_query_are_safe():
    assert top_chunks({}, "salary") == {}
    assert top_chunks(PAGES, "", k=1, size=200)


def test_research_uses_retrieval_only_when_enabled(monkeypatch):
    topic = research.TOPICS["salary"]
    monkeypatch.setattr(config, "USE_RETRIEVAL", False)
    plain = research._select_context(PAGES, topic, "Acme")
    assert plain["https://b.test"] == PAGES["https://b.test"][:config.PAGE_CHARS]
    monkeypatch.setattr(config, "USE_RETRIEVAL", True)
    picked = research._select_context(PAGES, topic, "Acme")
    assert sum(len(t) for t in picked.values()) < sum(len(t) for t in plain.values())
