"""(family_a, family_b) -> affinity [0,1], symmetric - see
docs/superpowers/specs/2026-10-01-scoring-redesign-design.md section 1
("Adjacency"). Computed from the job corpus, never authored by hand: each
family's IDF-weighted skill-presence vector, then cosine similarity
between every pair. Pure: the corpus query and the file write live in
cli.py, same split as idf.py.
"""

from __future__ import annotations

import math

from jobfit.ats_scorer.taxonomy import AFFINITY_FLOOR, load_skills_taxonomy


def build_family_skill_vectors(texts_by_family: dict[str, list[str]], idf: dict[str, float]) -> dict[str, dict[str, float]]:
    """{family: {skill: presence_fraction * idf}} - a skill every job in the
    family mentions, weighted by how rare it is corpus-wide, so a skill
    common to every family (Python) barely moves similarity and a skill
    only two adjacent families share moves it a lot.

    Args:
        texts_by_family: {family: [job text, ...]} - one string per job
            (title + description, or however the caller already builds
            the corpus text elsewhere).
        idf: {skill: idf}, from skill_idf.json.

    Returns:
        {family: {skill: weight}}, only non-zero entries kept.
    """
    taxonomy = load_skills_taxonomy()
    skill_names = [entry["canonical"] for entry in taxonomy.entries]
    vectors: dict[str, dict[str, float]] = {}
    for family, texts in texts_by_family.items():
        if not texts:
            vectors[family] = {}
            continue
        counts = dict.fromkeys(skill_names, 0)
        for text in texts:
            for skill in set(taxonomy.find_in_text(text)):
                counts[skill] += 1
        n = len(texts)
        vectors[family] = {
            skill: (count / n) * idf.get(skill, 0.0)
            for skill, count in counts.items() if count > 0
        }
    return vectors


def cosine_similarity(a: dict[str, float], b: dict[str, float]) -> float:
    """Cosine similarity of two sparse {skill: weight} vectors, 0.0 when
    either is empty (a family with no corpus presence yet shares nothing
    with anyone, by construction)."""
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    dot = sum(a[k] * b[k] for k in common)
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def compute_adjacency_matrix(
    texts_by_family: dict[str, list[str]], idf: dict[str, float], families: list[str],
) -> dict[str, dict[str, float]]:
    """The full symmetric matrix, floor applied: same family is 1.0 by
    definition (not computed), every other pair is at least AFFINITY_FLOOR.

    Args:
        texts_by_family: {family: [job text, ...]}.
        idf: {skill: idf}.
        families: Every family name the matrix must cover (role_families.json's
            key set), so a family with zero corpus presence still gets a
            full (floored) row rather than being absent.

    Returns:
        {family_a: {family_b: affinity}}.
    """
    vectors = build_family_skill_vectors(texts_by_family, idf)
    matrix: dict[str, dict[str, float]] = {a: {} for a in families}
    for a in families:
        for b in families:
            if a == b:
                matrix[a][b] = 1.0
            else:
                matrix[a][b] = max(cosine_similarity(vectors.get(a, {}), vectors.get(b, {})), AFFINITY_FLOOR)
    return matrix
