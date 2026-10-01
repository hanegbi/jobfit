"""adjacency.py's pure computation - build_family_skill_vectors(),
cosine_similarity(), compute_adjacency_matrix(). Never touches the store;
the CLI command (compute-adjacency) owns the corpus query."""

import pytest

from jobfit.ats_scorer.adjacency import (
    build_family_skill_vectors, compute_adjacency_matrix, cosine_similarity,
)
from jobfit.ats_scorer.taxonomy import AFFINITY_FLOOR


def test_identical_vectors_have_cosine_similarity_1():
    v = {"Python": 2.0, "Kubernetes": 3.0}
    assert cosine_similarity(v, v) == pytest.approx(1.0)


def test_orthogonal_vectors_have_cosine_similarity_0():
    assert cosine_similarity({"Python": 1.0}, {"Rust": 1.0}) == 0.0


def test_an_empty_vector_has_cosine_similarity_0_not_an_error():
    assert cosine_similarity({}, {"Python": 1.0}) == 0.0
    assert cosine_similarity({"Python": 1.0}, {}) == 0.0


def test_build_family_skill_vectors_weights_presence_by_idf():
    texts_by_family = {"backend": ["Python Python Kubernetes", "Python only"]}
    idf = {"Python": 2.0, "Kubernetes": 5.0}
    vectors = build_family_skill_vectors(texts_by_family, idf)
    # Python: present in 2/2 docs -> 1.0 presence * idf 2.0 = 2.0
    # Kubernetes: present in 1/2 docs -> 0.5 presence * idf 5.0 = 2.5
    assert vectors["backend"]["Python"] == 2.0
    assert vectors["backend"]["Kubernetes"] == 2.5


def test_build_family_skill_vectors_gives_an_empty_vector_for_a_family_with_no_texts():
    vectors = build_family_skill_vectors({"ml_infra": []}, {"Python": 2.0})
    assert vectors["ml_infra"] == {}


def test_compute_adjacency_matrix_is_1_on_the_diagonal_by_definition():
    texts_by_family = {"backend": ["Python"], "frontend": ["React"]}
    idf = {"Python": 2.0, "React": 2.0}
    matrix = compute_adjacency_matrix(texts_by_family, idf, ["backend", "frontend"])
    assert matrix["backend"]["backend"] == 1.0
    assert matrix["frontend"]["frontend"] == 1.0


def test_compute_adjacency_matrix_is_symmetric():
    texts_by_family = {"backend": ["Python Kubernetes"], "frontend": ["React Python"]}
    idf = {"Python": 2.0, "Kubernetes": 5.0, "React": 3.0}
    matrix = compute_adjacency_matrix(texts_by_family, idf, ["backend", "frontend"])
    assert matrix["backend"]["frontend"] == matrix["frontend"]["backend"]


def test_compute_adjacency_matrix_floors_every_off_diagonal_pair():
    texts_by_family = {"backend": ["Python"], "legal": ["Compliance"]}
    idf = {"Python": 2.0, "Compliance": 2.0}
    matrix = compute_adjacency_matrix(texts_by_family, idf, ["backend", "legal"])
    assert matrix["backend"]["legal"] == AFFINITY_FLOOR


def test_compute_adjacency_matrix_covers_every_family_even_with_zero_corpus_presence():
    matrix = compute_adjacency_matrix({}, {}, ["backend", "ml_infra"])
    assert matrix["backend"]["ml_infra"] == AFFINITY_FLOOR
    assert matrix["ml_infra"]["ml_infra"] == 1.0
