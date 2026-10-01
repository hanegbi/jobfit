"""family_fit(candidate_vector, job_family, adjacency) - see
jobfit/ats_scorer/family_fit.py and
docs/superpowers/specs/2026-10-01-scoring-redesign-design.md section 5."""

from jobfit.ats_scorer.family_fit import UNKNOWN_FAMILY_FIT, family_fit
from jobfit.ats_scorer.taxonomy import AFFINITY_FLOOR

ADJACENCY = {
    "backend": {"backend": 1.0, "fullstack": 0.7, "frontend": 0.2, "sales": 0.1},
    "fullstack": {"fullstack": 1.0, "backend": 0.7, "frontend": 0.2, "sales": 0.1},
}


def test_full_affinity_in_the_jobs_own_family_gives_family_fit_1():
    assert family_fit({"backend": 1.0}, "backend", ADJACENCY) == 1.0


def test_an_adjacent_family_is_credited_rather_than_scored_zero():
    assert family_fit({"fullstack": 1.0}, "backend", ADJACENCY) == 0.7


def test_the_strongest_available_path_wins_across_multiple_candidate_families():
    vector = {"fullstack": 0.6, "frontend": 0.4}
    # fullstack->backend 0.7*0.6=0.42 beats frontend->backend 0.2*0.4=0.08
    assert family_fit(vector, "backend", ADJACENCY) == 0.42


def test_an_unknown_job_family_is_neutral_not_a_penalty():
    assert family_fit({"sales": 1.0}, None, ADJACENCY) == UNKNOWN_FAMILY_FIT


def test_an_empty_affinity_vector_gives_family_fit_zero():
    assert family_fit({}, "backend", ADJACENCY) == 0.0


def test_zero_weight_families_in_the_vector_are_skipped():
    assert family_fit({"backend": 0.0, "fullstack": 1.0}, "backend", ADJACENCY) == 0.7


def test_a_pair_missing_from_the_adjacency_table_falls_back_to_the_floor():
    assert family_fit({"legal": 1.0}, "backend", ADJACENCY) == AFFINITY_FLOOR
