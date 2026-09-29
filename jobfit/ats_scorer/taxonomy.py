"""Loads the skills taxonomy and role-family keyword lists once and exposes
lookup helpers for the extractors and matcher.
"""

import json
import re
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
SKILLS_TAXONOMY_PATH = DATA_DIR / "skills_taxonomy.json"
ROLE_FAMILIES_PATH = DATA_DIR / "role_families.json"


class SkillsTaxonomy:
    """Canonical-name/alias lookup over the curated skills list.

    Attributes:
        entries: The raw taxonomy entries as loaded from the data file.
    """

    def __init__(self, entries: list[dict]):
        """Build alias-to-canonical and canonical-to-pattern lookup tables.

        Args:
            entries: Taxonomy entries, each {"canonical", "aliases", "category"}.
        """
        self.entries = entries
        self._alias_to_canonical: dict[str, str] = {}
        self._canonical_to_patterns: dict[str, list[re.Pattern]] = {}
        for entry in entries:
            canonical = entry["canonical"]
            patterns = []
            for alias in entry["aliases"]:
                self._alias_to_canonical[alias.lower()] = canonical
                patterns.append(re.compile(r"\b" + re.escape(alias.lower()) + r"\b"))
            self._canonical_to_patterns[canonical] = patterns

    def find_in_text(self, text: str) -> list[str]:
        """Return every canonical skill whose alias appears in text.

        Args:
            text: Text to search.

        Returns:
            Canonical skill names found, in taxonomy order, deduplicated.
        """
        if not text:
            return []
        lowered = text.lower()
        found = []
        for canonical, patterns in self._canonical_to_patterns.items():
            if any(p.search(lowered) for p in patterns):
                found.append(canonical)
        return found


class RoleFamilies:
    """Keyword-based role-family classifier.

    Attributes:
        families: {family_name: [keyword, ...]}.
    """

    def __init__(self, families: dict[str, list[str]]):
        """Compile per-family keyword patterns.

        Args:
            families: {family_name: [keyword, ...]} as loaded from the data file.
        """
        self.families = families
        self._patterns: dict[str, list[re.Pattern]] = {
            name: [re.compile(r"\b" + re.escape(kw.lower()) + r"\b") for kw in keywords]
            for name, keywords in families.items()
        }

    def classify(self, *texts: str) -> str | None:
        """Return the family with the highest keyword-hit count across texts.

        Args:
            *texts: One or more text blocks to search (e.g. title, bullets).

        Returns:
            The best-matching family name, or None if no keyword matched.
        """
        joined = " ".join(t.lower() for t in texts if t)
        if not joined:
            return None
        best_family = None
        best_count = 0
        for family, patterns in self._patterns.items():
            count = sum(1 for p in patterns if p.search(joined))
            if count > best_count:
                best_count = count
                best_family = family
        return best_family


@lru_cache(maxsize=1)
def load_skills_taxonomy() -> SkillsTaxonomy:
    """Load and cache the skills taxonomy from its data file.

    Returns:
        A SkillsTaxonomy built from jobfit/ats_scorer/data/skills_taxonomy.json.
    """
    entries = json.loads(SKILLS_TAXONOMY_PATH.read_text(encoding="utf-8"))
    return SkillsTaxonomy(entries)


@lru_cache(maxsize=1)
def load_role_families() -> RoleFamilies:
    """Load and cache the role-family keyword lists from their data file.

    Returns:
        A RoleFamilies built from jobfit/ats_scorer/data/role_families.json.
    """
    families = json.loads(ROLE_FAMILIES_PATH.read_text(encoding="utf-8"))
    return RoleFamilies(families)
