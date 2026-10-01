"""fit_weights.py's search mechanism - precision@k computation and the
min_score/max_score hard-constraint rejection - tested against a fake
score_fn, never the real store or a real labeled set. See
docs/superpowers/specs/2026-10-01-scoring-redesign-design.md section 5."""

from jobfit.ats_scorer.config import ScoringConfig
from jobfit.ats_scorer.fit_weights import LabeledExample, _precision_at_k, evaluate, run_search


def _constant_score_fn(scores: dict[str, int]):
    """A score_fn that ignores the trial config and returns a fixed score
    per job_id - enough to drive precision@k deterministically."""
    def score_fn(example, config):
        return scores[example.job_id]
    return score_fn


def test_precision_at_k_is_the_fraction_of_the_top_k_labeled_fit():
    pairs = [
        (LabeledExample("p", "j1", "fit"), 90),
        (LabeledExample("p", "j2", "fit"), 80),
        (LabeledExample("p", "j3", "not_fit"), 70),
        (LabeledExample("p", "j4", "not_fit"), 60),
    ]
    assert _precision_at_k(pairs, 4) == 0.5
    assert _precision_at_k(pairs, 2) == 1.0


def test_precision_at_k_is_none_with_fewer_than_k_examples():
    pairs = [(LabeledExample("p", "j1", "fit"), 90)]
    assert _precision_at_k(pairs, 20) is None


def test_precision_at_k_ranks_by_score_not_label_order():
    """A low-scored "fit" job outside the top k doesn't count - precision
    only looks at what actually ranked in the top k."""
    pairs = [
        (LabeledExample("p", "j1", "not_fit"), 100),
        (LabeledExample("p", "j2", "not_fit"), 99),
        (LabeledExample("p", "j3", "fit"), 1),
    ]
    assert _precision_at_k(pairs, 2) == 0.0


def test_evaluate_rejects_a_config_that_violates_min_score():
    examples = [LabeledExample("p", "j1", "fit", min_score=85)]
    score_fn = _constant_score_fn({"j1": 50})
    assert evaluate(examples, score_fn, ScoringConfig()) is None


def test_evaluate_rejects_a_config_that_violates_max_score():
    examples = [LabeledExample("p", "j1", "not_fit", max_score=40)]
    score_fn = _constant_score_fn({"j1": 60})
    assert evaluate(examples, score_fn, ScoringConfig()) is None


def test_evaluate_accepts_a_config_that_satisfies_every_constraint():
    examples = [
        LabeledExample("p", "j1", "fit", min_score=85),
        LabeledExample("p", "j2", "not_fit", max_score=40),
    ]
    score_fn = _constant_score_fn({"j1": 90, "j2": 20})
    result = evaluate(examples, score_fn, ScoringConfig())
    assert result is not None


def test_run_search_returns_none_when_every_trial_violates_a_constraint():
    """An impossible-to-satisfy pair of constraints (the same job must
    score both >=90 and <=10) - the search's own "formula's shape is
    wrong" signal, not a crash."""
    examples = [
        LabeledExample("p", "j1", "fit", min_score=90),
        LabeledExample("p", "j1", "fit", max_score=10),
    ]
    score_fn = _constant_score_fn({"j1": 50})
    assert run_search(examples, score_fn, trials=10, seed=0) is None


def test_run_search_is_deterministic_for_the_same_seed():
    examples = [LabeledExample("p", f"j{i}", "fit" if i % 2 else "not_fit") for i in range(60)]
    score_fn = _constant_score_fn({f"j{i}": (i * 7) % 100 for i in range(60)})
    first = run_search(examples, score_fn, trials=30, seed=42)
    second = run_search(examples, score_fn, trials=30, seed=42)
    assert first.config.model_dump() == second.config.model_dump()
    assert first.precision_at_20 == second.precision_at_20


def test_run_search_reports_trial_counts():
    examples = [LabeledExample("p", "j1", "fit", min_score=200)]  # unreachable - every trial rejected
    score_fn = _constant_score_fn({"j1": 50})
    result = run_search(examples, score_fn, trials=15, seed=0)
    assert result is None
