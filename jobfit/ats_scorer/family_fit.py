"""family_fit(candidate_vector, job_family, adjacency) -> [0, 1]: how well
the candidate's weighted work history fits a job's family, crediting
adjacent families (devops_sre work counts for an infrastructure_platform
job) rather than scoring anything but an exact match as 0. See
docs/superpowers/specs/2026-10-01-scoring-redesign-design.md section 5.
"""

from __future__ import annotations

from jobfit.ats_scorer.taxonomy import AFFINITY_FLOOR

# Neither helped nor hurt - job.family is "unknown" (never guessed at, per
# the taxonomy's own terminal-unknown rule), so there is nothing to compare
# the candidate's vector against. Multiplying by 1.0 would let an
# unclassified job inherit whatever job_fit alone says; multiplying by 0.0
# would let the family_fit<0.3 gate zero out a job that might genuinely be
# a fit. 0.5 is neutral in both directions.
UNKNOWN_FAMILY_FIT = 0.5


def family_fit(
    family_affinity: dict[str, float], job_family: str | None, adjacency: dict[str, dict[str, float]],
) -> float:
    """The best-supported path from the candidate's experience to this
    job's family: for every family the candidate has weighted affinity in,
    how much of that affinity carries over (1.0 for the same family, the
    computed/floored adjacency otherwise), and the strongest path wins.

    Args:
        family_affinity: The candidate's full affinity vector (see
            ats_scorer/profile.py) - {family: weight in [0, 1]}.
        job_family: The job's own family, or None when unknown.
        adjacency: {family_a: {family_b: affinity}}, from
            family_adjacency.json (ats_scorer/adjacency.py).

    Returns:
        A value in [0, 1].
    """
    if job_family is None:
        return UNKNOWN_FAMILY_FIT
    best = 0.0
    for family, weight in family_affinity.items():
        if weight <= 0:
            continue
        affinity = 1.0 if family == job_family else adjacency.get(family, {}).get(job_family, AFFINITY_FLOOR)
        best = max(best, weight * affinity)
    return best
