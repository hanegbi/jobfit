"""BM25 over page chunks: send the model the few passages that matter, not whole pages.

No embeddings. Pure-Python ranking costs nothing on a CPU-only machine, and for the
keyword-shaped questions here (salary, interview, funding) it is enough. Prompt
reading is the slow part of local inference, so cutting the prompt cuts the wait.
"""

import re

from rank_bm25 import BM25Okapi

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def _chunks(text: str, size: int) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)] or [""]


def top_chunks(pages: dict[str, str], query: str, *, k: int = 4, size: int = 900) -> dict[str, str]:
    """{url: the best-matching passages of that page}, at most k passages overall."""
    entries = [(url, index, chunk) for url, text in pages.items() for index, chunk in enumerate(_chunks(text, size))]
    if not entries:
        return {}
    scores = BM25Okapi([_tokens(chunk) or [""] for _, _, chunk in entries]).get_scores(_tokens(query))
    best = sorted(range(len(entries)), key=lambda i: scores[i], reverse=True)[:k]
    chosen: dict[str, list[tuple[int, str]]] = {}
    for i in sorted(best, key=lambda i: (entries[i][0], entries[i][1])):
        url, index, chunk = entries[i]
        chosen.setdefault(url, []).append((index, chunk))
    return {url: " … ".join(chunk for _, chunk in parts) for url, parts in chosen.items()}
