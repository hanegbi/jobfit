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
CANONICAL_TITLES_PATH = DATA_DIR / "canonical_titles.json"
SKILL_IDF_PATH = DATA_DIR / "skill_idf.json"
FAMILY_ADJACENCY_PATH = DATA_DIR / "family_adjacency.json"
# Two families with almost no shared vocabulary still get this much credit,
# rather than rounding to 0 - see docs/superpowers/specs/2026-10-01-scoring
# -redesign-design.md section 1 ("Adjacency"). Applied once, when
# compute-adjacency writes family_adjacency.json, and again defensively in
# family_fit.py for any pair the file doesn't have an entry for at all.
AFFINITY_FLOOR = 0.1


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
        """Return the family with the highest keyword-hit count across texts,
        breaking a tie in favour of whichever matched EARLIEST.

        A title names its role first and qualifies it afterwards, so the
        earlier match is the head noun and the later one describes what the
        role works on. Without the tie-break "Product Manager - Connectors
        and AI Infrastructure" scored 1-1 between product and ml_infra and
        was decided by whichever family happened to come first in
        role_families.json - ml_infra, which put a product role in a
        backend engineer's top band at 86.

        Args:
            *texts: One or more text blocks to search (e.g. title, bullets).

        Returns:
            The best-matching family name, or None if no keyword matched.
        """
        joined = " ".join(t.lower() for t in texts if t)
        if not joined:
            return None
        best_key: tuple[int, int] | None = None
        best_family = None
        for family, patterns in self._patterns.items():
            starts = [match.start() for match in (p.search(joined) for p in patterns) if match]
            if not starts:
                continue
            key = (len(starts), -min(starts))
            if best_key is None or key > best_key:
                best_key, best_family = key, family
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


@lru_cache(maxsize=1)
def load_skill_idf() -> dict[str, float]:
    """{canonical skill: idf}, from the file `ats_scorer.cli compute-idf`
    writes - see ats_scorer/idf.py. Empty (not an error) when the command
    has never been run, so a fresh checkout degrades to "every skill
    weighted the same" rather than crashing."""
    if not SKILL_IDF_PATH.exists():
        return {}
    return json.loads(SKILL_IDF_PATH.read_text(encoding="utf-8"))["weights"]


@lru_cache(maxsize=1)
def load_family_adjacency() -> dict[str, dict[str, float]]:
    """{family_a: {family_b: affinity}}, symmetric, from the file
    `ats_scorer.cli compute-adjacency` writes - see ats_scorer/adjacency.py.
    Every pair already has the floor/overrides baked in by that command;
    this is a straight read. Empty when the command has never been run, so
    family_fit() degrades to "every non-identical family at the floor"
    rather than crashing."""
    if not FAMILY_ADJACENCY_PATH.exists():
        return {}
    return json.loads(FAMILY_ADJACENCY_PATH.read_text(encoding="utf-8"))["matrix"]
