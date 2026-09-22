"""Extract CV text and a skills-based profile from a .docx resume."""

import re

import docx

from jobfit import config


def extract_text(path) -> str:
    """Return all paragraph and table-cell text from a .docx file."""
    document = docx.Document(str(path))
    parts = [p.text for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                if cell.text.strip():
                    parts.append(cell.text)
    return "\n".join(parts)


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


def load_profiles() -> dict[str, dict]:
    """Load both the default and infra CV profiles."""
    return {
        "default": build_profile(config.CV_DEFAULT),
        "infra": build_profile(config.CV_INFRA),
    }
