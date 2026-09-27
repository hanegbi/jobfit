"""translation.py detects Hebrew job listings and translates them to English
so scoring's coverage check (which runs against an English CV vocabulary)
doesn't silently zero out every job from a Hebrew-only career site."""

from jobfit import translation


# --- contains_hebrew ---------------------------------------------------------

def test_contains_hebrew_true_for_hebrew_text():
    assert translation.contains_hebrew("מחסנאי תחמושת") is True


def test_contains_hebrew_false_for_english_text():
    assert translation.contains_hebrew("Senior Backend Engineer") is False


def test_contains_hebrew_false_for_empty_or_none():
    assert translation.contains_hebrew("") is False
    assert translation.contains_hebrew(None) is False


def test_contains_hebrew_true_for_mixed_text():
    assert translation.contains_hebrew("Backend Engineer - מהנדס תוכנה") is True


def test_contains_hebrew_false_for_mostly_english_text_with_one_stray_hebrew_word():
    """Real bug caught live: a company's job description was almost
    entirely English but included a language-switcher link ("EN עברית"),
    and that single stray Hebrew word triggered a full (and needless,
    partially-garbling) translation of otherwise-fine English content."""
    text = "We are hiring a backend engineer with strong Python experience. " * 20 + "EN עברית"
    assert translation.contains_hebrew(text) is False


# --- _split_into_chunks ------------------------------------------------------

def test_split_into_chunks_keeps_short_text_as_one_chunk():
    assert translation._split_into_chunks("short text.") == ["short text."]


def test_split_into_chunks_respects_the_max_char_limit():
    text = "This is a sentence. " * 40  # ~800 chars
    chunks = translation._split_into_chunks(text, max_chars=100)
    assert all(len(c) <= 100 for c in chunks)
    assert len(chunks) > 1


def test_split_into_chunks_hard_wraps_a_single_run_on_sentence():
    text = "x" * 1000  # one giant "sentence" with no punctuation at all
    chunks = translation._split_into_chunks(text, max_chars=480)
    assert all(len(c) <= 480 for c in chunks)
    assert "".join(chunks) == text


# --- translate_to_english ----------------------------------------------------

def test_translate_to_english_returns_english_text_unchanged():
    assert translation.translate_to_english("Senior Backend Engineer") == "Senior Backend Engineer"


def test_translate_to_english_uses_the_cache_without_calling_the_translator(monkeypatch, tmp_path):
    """The cache check happens before deep_translator is even imported, so a
    cache hit must never reach the network - this is what keeps a re-scrape
    of an unchanged Hebrew job from re-spending MyMemory's daily quota."""
    cache_path = tmp_path / "translations.json"
    cache_path.write_text(
        '{"' + translation._cache_key("מהנדס תוכנה") + '": "Software Engineer"}', encoding="utf-8",
    )
    monkeypatch.setattr(translation, "CACHE_PATH", cache_path)

    assert translation.translate_to_english("מהנדס תוכנה") == "Software Engineer"


def test_translate_to_english_falls_back_to_the_original_chunk_when_the_backend_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(translation, "CACHE_PATH", tmp_path / "translations.json")

    class _BoomTranslator:
        def __init__(self, *a, **kw):
            pass

        def translate(self, text):
            raise RuntimeError("service unavailable")

    import deep_translator
    monkeypatch.setattr(deep_translator, "MyMemoryTranslator", _BoomTranslator)
    monkeypatch.setattr(translation.time, "sleep", lambda *_: None)

    result = translation.translate_to_english("מהנדס תוכנה")

    assert result == "מהנדס תוכנה"  # kept the original since translation failed


# --- translate_job_if_needed --------------------------------------------------

def test_translate_job_if_needed_leaves_english_jobs_untouched():
    job = {"title": "Backend Engineer", "description": "We need Python."}
    result = translation.translate_job_if_needed(dict(job))
    assert result == job
    assert "title_original" not in result


def test_translate_job_if_needed_stashes_originals_and_sets_source_language(monkeypatch, tmp_path):
    monkeypatch.setattr(translation, "CACHE_PATH", tmp_path / "translations.json")
    monkeypatch.setattr(translation, "translate_to_english", lambda text: f"[EN] {text}")

    job = {"title": "מהנדס תוכנה", "description": "תיאור בעברית"}
    result = translation.translate_job_if_needed(job)

    assert result["title"] == "[EN] מהנדס תוכנה"
    assert result["description"] == "[EN] תיאור בעברית"
    assert result["title_original"] == "מהנדס תוכנה"
    assert result["description_original"] == "תיאור בעברית"
    assert result["source_language"] == "he"
