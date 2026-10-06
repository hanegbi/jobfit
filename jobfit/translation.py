"""Best-effort translation of non-English (currently: Hebrew) job listings
into English.

Scoring's coverage check (70% of a job's score) runs the description
against an English CV's vocabulary - a Hebrew description scores ~0
regardless of how relevant the actual job is, so a Hebrew-only career site
(e.g. Elbit Systems Sigmabit) would otherwise silently under-score every
one of its jobs. Uses MyMemory's free translate API (no key required) via
deep-translator; Google's free endpoint reliably 429s from this network, so
MyMemory is the primary (and only) backend rather than a fallback.

Translations are cached to disk by content hash (cache/translations.json)
since MyMemory's free tier has a modest daily word quota - re-scraping an
unchanged job must not re-spend it.
"""

import hashlib
import json
import logging
import re
import threading
import time

from jobfit import config
from jobfit.atomic_io import write_json_atomic

logger = logging.getLogger("jobfit.translation")

HEBREW_RE = re.compile(r"[֐-׿]")
_LETTER_RE = re.compile(r"[A-Za-z֐-׿]")
_CHUNK_MAX_CHARS = 480
_REQUEST_DELAY_S = 0.35
# Retry a rate-limited chunk rather than abandoning it: 1s, 2s, 4s, 8s.
_MAX_ATTEMPTS = 5
_RETRY_BASE_DELAY_S = 1.0
# A page that's overwhelmingly English but happens to include one Hebrew
# word (e.g. an "EN | עברית" language-switcher link) must not trigger a
# full translate - real case caught live: a company's English job
# description got needlessly run through translation, and the mostly-
# English text partially garbled, because contains_hebrew() alone treats a
# single stray Hebrew character anywhere in the text as "this is Hebrew."
# Requiring Hebrew to be a real share of the letters distinguishes actual
# Hebrew content from an incidental fragment.
MIN_HEBREW_LETTER_RATIO = 0.2

CACHE_PATH = config.ROOT / "cache" / "translations.json"

_cache_lock = threading.Lock()


def contains_hebrew(text: str) -> bool:
    """Whether Hebrew makes up a real share of text's letters, not just a
    single incidental character somewhere in an otherwise non-Hebrew page."""
    if not text:
        return False
    letters = _LETTER_RE.findall(text)
    if not letters:
        return False
    hebrew_count = sum(1 for c in letters if HEBREW_RE.match(c))
    return (hebrew_count / len(letters)) >= MIN_HEBREW_LETTER_RATIO


_cache_memo: dict | None = None
_cache_memo_path = None


def _load_cache() -> dict:
    """The on-disk cache, read once per process (and per CACHE_PATH) and kept
    in memory: a full run looks up tens of thousands of jobs, and re-parsing
    a multi-MB JSON file on every lookup was a measurable share of run time."""
    global _cache_memo, _cache_memo_path
    if _cache_memo is not None and _cache_memo_path == CACHE_PATH:
        return _cache_memo
    cache: dict = {}
    if CACHE_PATH.exists():
        try:
            cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            cache = {}
    _cache_memo, _cache_memo_path = cache, CACHE_PATH
    return cache


def _cache_key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _split_into_chunks(text: str, max_chars: int = _CHUNK_MAX_CHARS) -> list[str]:
    """Break text into pieces under MyMemory's 500-char per-request limit,
    preferring sentence boundaries so translation quality doesn't suffer
    from mid-sentence cuts."""
    sentences = re.split(r"(?<=[.!?])\s+|\n+", text)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            for i in range(0, len(sentence), max_chars):
                chunks.append(sentence[i:i + max_chars])
            continue
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) > max_chars:
            chunks.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _translate_chunk(translator, chunk: str) -> str | None:
    """One chunk translated, or None when the service would not do it.

    MyMemory rate-limits per second, and a flat delay between calls only
    postpones the problem: a batch run hit the limit after 300 titles and
    then failed the remaining 544 in a row, because nothing ever backed off.
    Each retry waits longer, so a burst recovers instead of burning the rest
    of the run.
    """
    delay = _RETRY_BASE_DELAY_S
    for attempt in range(_MAX_ATTEMPTS):
        try:
            return translator.translate(chunk)
        except Exception as error:  # noqa: BLE001 - any failure is retried the same way
            if attempt == _MAX_ATTEMPTS - 1:
                logger.debug("translation chunk failed after %d attempts: %s", _MAX_ATTEMPTS, error)
                return None
            time.sleep(delay)
            delay *= 2
    return None


def translate_to_english(text: str) -> str:
    """Translate Hebrew text to English. Returns the original text unchanged
    if it has no Hebrew, or if translation fails/hits a quota - a scrape run
    must never abort over a translation-service outage.

    A failure is never cached. MyMemory rate-limits, and caching the Hebrew
    original as though it were the translation makes the failure permanent:
    the entry is a cache hit forever after, so the job stays in Hebrew
    through every later run. 2,039 of 2,080 cached entries were poisoned
    this way before this guard existed.
    """
    if not contains_hebrew(text):
        return text

    with _cache_lock:
        cache = _load_cache()
        key = _cache_key(text)
        cached = cache.get(key)
    if cached is not None:
        return cached

    from deep_translator import MyMemoryTranslator

    translator = MyMemoryTranslator(source="he-IL", target="en-GB")
    translated_chunks = []
    failed = False
    for chunk in _split_into_chunks(text):
        translated = _translate_chunk(translator, chunk)
        translated_chunks.append(translated if translated is not None else chunk)
        failed = failed or translated is None
        time.sleep(_REQUEST_DELAY_S)

    result = " ".join(translated_chunks)
    # Still Hebrew means the service answered with the input (it does that
    # under load) - indistinguishable from a failure, and just as wrong to keep.
    if failed or contains_hebrew(result):
        logger.debug("translation incomplete, not caching: %r", text[:60])
        return result
    with _cache_lock:
        cache = _load_cache()
        cache[key] = result
        write_json_atomic(CACHE_PATH, cache)
    return result


_LATIN_RE = re.compile(r"[A-Za-z]")


def english_name(name: str) -> str:
    """A company's name with its Hebrew half dropped.

    Israeli companies routinely register bilingually - "Discount Bank בנק
    דיסקונט", "Ness Technologies | נס טכנולוגיות" - where the two halves are
    the same name twice, not two facts. Dropping the Hebrew words loses
    nothing and keeps the app English. A name with no Latin word at all is
    returned unchanged: there is nothing there to choose between, and
    translating a proper noun invents a company that does not exist.
    """
    text = " ".join((name or "").split())
    if not text or not HEBREW_RE.search(text):
        return text
    kept = [word for word in text.split() if _LATIN_RE.search(word) and not HEBREW_RE.search(word)]
    if not kept:
        return text
    # Separators that survived only because the word beside them went.
    return " ".join(kept).strip(" |-,/")


def poisoned_cache_keys(cache: dict) -> list[str]:
    """Cache entries whose "translation" is still Hebrew - failures written
    before translate_to_english refused to cache them. Dropping one makes
    that text eligible for translation again on the next run."""
    return [key for key, value in cache.items() if contains_hebrew(value or "")]


def translate_job_if_needed(job: dict) -> dict:
    """Mutates job in place: if its title or description contains Hebrew,
    translates both to English and stashes the Hebrew originals under
    *_original plus a source_language marker the UI can show as a badge.

    config.TRANSLATION_ENABLED turns the network calls off while still
    marking the job as Hebrew, so a run is never held up by MyMemory. The
    marker and the *_original fields are what let a later pass translate
    exactly the jobs that were skipped - without them a skipped job would
    be indistinguishable from an English one. A free API that rate-limits
    had workers sleeping through 1->2->4->8s backoff per 480-char chunk,
    which is minutes per long Hebrew description and was the slowest thing
    in a full run by a wide margin.
    """
    title = job.get("title") or ""
    description = job.get("description") or ""
    if not (contains_hebrew(title) or contains_hebrew(description)):
        return job

    if not config.TRANSLATION_ENABLED:
        job["title_original"] = title
        job["description_original"] = description
        job["source_language"] = "he"
        return job

    job["title_original"] = title
    job["description_original"] = description
    job["source_language"] = "he"
    if title:
        job["title"] = translate_to_english(title)
    if description:
        job["description"] = translate_to_english(description)
    return job
