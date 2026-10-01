"""Skill IDF (inverse document frequency) over the job corpus.

See docs/superpowers/specs/2026-10-01-scoring-redesign-design.md section 4.
Computed once, from the job corpus only - never a CV. Pure: takes JD text
strings, returns {skill: idf}. The corpus query and the file write live in
cli.py, which is allowed to reach into jobfit.store; this module only ever
sees strings, same as the rest of this package.
"""

from __future__ import annotations

import math

from jobfit.ats_scorer.taxonomy import load_skills_taxonomy


def compute_idf(texts: list[str]) -> dict[str, float]:
    """idf = ln(corpus_size / document_frequency) for every taxonomy skill.

    A skill matched by nothing in the corpus gets ln(corpus_size) (the
    df=1 floor) rather than an undefined or infinite value, so it still
    ranks as maximally rare instead of silently missing from the output -
    the taxonomy's own skill list is always the full key set.

    Args:
        texts: One string per job (title + description, or just
            description - whatever the caller already searches with
            RoleFamilies/SkillsTaxonomy elsewhere).

    Returns:
        {canonical skill name: idf}, one entry per skill in the taxonomy.
    """
    taxonomy = load_skills_taxonomy()
    corpus_size = len(texts)
    document_frequency = {entry["canonical"]: 0 for entry in taxonomy.entries}
    if corpus_size == 0:
        return {skill: 0.0 for skill in document_frequency}

    for text in texts:
        for skill in set(taxonomy.find_in_text(text)):
            document_frequency[skill] += 1

    return {
        skill: math.log(corpus_size / count) if count > 0 else math.log(corpus_size)
        for skill, count in document_frequency.items()
    }
