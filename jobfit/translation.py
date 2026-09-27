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
_CHUNK_MAX_CHARS = 480
_REQUEST_DELAY_S = 0.35

CACHE_PATH = config.ROOT / "cache" / "translations.json"

_cache_lock = threading.Lock()


def contains_hebrew(text: str) -> bool:
    return bool(text) and bool(HEBREW_RE.search(text))


def _load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


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


def translate_to_english(text: str) -> str:
    """Translate Hebrew text to English. Returns the original text unchanged
    if it has no Hebrew, or if translation fails/hits a quota - a scrape run
    must never abort over a translation-service outage."""
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
    for chunk in _split_into_chunks(text):
        try:
            translated_chunks.append(translator.translate(chunk))
        except Exception as error:  # noqa: BLE001
            logger.debug("translation chunk failed, keeping original: %s", error)
            translated_chunks.append(chunk)
        time.sleep(_REQUEST_DELAY_S)

    result = " ".join(translated_chunks)
    with _cache_lock:
        cache = _load_cache()
        cache[key] = result
        write_json_atomic(CACHE_PATH, cache)
    return result


def translate_job_if_needed(job: dict) -> dict:
    """Mutates job in place: if its title or description contains Hebrew,
    translates both to English and stashes the Hebrew originals under
    *_original plus a source_language marker the UI can show as a badge."""
    title = job.get("title") or ""
    description = job.get("description") or ""
    if not (contains_hebrew(title) or contains_hebrew(description)):
        return job

    job["title_original"] = title
    job["description_original"] = description
    job["source_language"] = "he"
    if title:
        job["title"] = translate_to_english(title)
    if description:
        job["description"] = translate_to_english(description)
    return job
