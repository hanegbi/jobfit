"""Extract CV text and a skills-based profile from a .docx or .pdf resume."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import docx
import pypdf

from jobfit import config
from jobfit.atomic_io import write_json_atomic


def _extract_text_docx(path: Path) -> str:
    document = docx.Document(str(path))
    parts = [p.text for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                if cell.text.strip():
                    parts.append(cell.text)
    return "\n".join(parts)


def _extract_text_pdf(path: Path) -> str:
    reader = pypdf.PdfReader(str(path))
    parts = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(p for p in parts if p.strip())


def extract_text(path) -> str:
    """Return the resume's text: .docx (paragraphs + table cells) or .pdf (page text)."""
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        return _extract_text_pdf(path)
    return _extract_text_docx(path)


def _word_in(term: str, text: str) -> bool:
    pattern = r"\b" + re.escape(term) + r"\b"
    return re.search(pattern, text, re.IGNORECASE) is not None


def extract_skills(text: str) -> list[str]:
    """Pull known tech skills out of CV text, longest terms first (for stable order)."""
    return list(dict.fromkeys(s for s in config.SKILLS_VOCAB if _word_in(s, text or "")))


def build_profile(cv_path) -> dict:
    """Build a {must_have_keywords, text} profile from one CV file."""
    text = extract_text(cv_path)
    return {"must_have_keywords": extract_skills(text), "text": text}


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return slug or "profile"


def _unique_id(base: str, existing: set[str]) -> str:
    if base not in existing:
        return base
    n = 2
    while f"{base}_{n}" in existing:
        n += 1
    return f"{base}_{n}"


def load_registry() -> dict[str, dict]:
    if config.CV_PROFILES_REGISTRY.exists():
        return json.loads(config.CV_PROFILES_REGISTRY.read_text(encoding="utf-8"))
    return {}


def save_registry(registry: dict[str, dict]) -> None:
    write_json_atomic(config.CV_PROFILES_REGISTRY, registry)


def register_profile(name: str, source_path: Path) -> str:
    """Copy source_path's CV into the registry under a new profile id. Returns that id."""
    source_path = Path(source_path)
    registry = load_registry()
    profile_id = _unique_id(_slugify(name), set(registry))
    config.CV_PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    dest = config.CV_PROFILES_DIR / f"{profile_id}{source_path.suffix}"
    dest.write_bytes(source_path.read_bytes())
    registry[profile_id] = {
        "name": name,
        "filename": dest.name,
        "uploaded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    save_registry(registry)
    return profile_id


def remove_profile(profile_id: str) -> None:
    registry = load_registry()
    entry = registry.pop(profile_id, None)
    if entry is None:
        return
    file_path = config.CV_PROFILES_DIR / entry["filename"]
    if file_path.exists():
        file_path.unlink()
    save_registry(registry)


def load_profiles() -> dict[str, dict]:
    """Build {must_have_keywords, text} for every registered CV profile."""
    return {
        profile_id: build_profile(config.CV_PROFILES_DIR / entry["filename"])
        for profile_id, entry in load_registry().items()
    }
