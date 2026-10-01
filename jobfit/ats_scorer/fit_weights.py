"""Search WeightConfig/FamilyFitConfig's free coefficients against a
labeled set, scored by precision@20 + precision@50, hard-rejecting any
configuration that violates a stated constraint. See
docs/superpowers/specs/2026-10-01-scoring-redesign-design.md section 5
("Weight tuning is a search script, not me").

Pure: takes already-scored examples in (LabeledExample + a score for a
given config), never touches a file or the store - the CLI command reads
the labels file and the job/CV text, this module only searches and scores.
"""

from __future__ import annotations

import random
from typing import Callable, NamedTuple

from jobfit.ats_scorer.config import FamilyFitConfig, ScoringConfig, WeightConfig

# (low, high) for every free coefficient this search moves. Bounds are
# mine to set (the spec's own instruction); the search picks the value.
WEIGHT_BOUNDS = {
    "must_have_coverage": (0.30, 0.60),
    "seniority_fit": (0.10, 0.30),
    "nice_to_have_coverage": (0.05, 0.20),
    "evidence_depth": (0.05, 0.20),
}
FAMILY_FIT_BOUNDS = {
    "gate_threshold": (0.20, 0.40),
    "gate_cap": (25, 45),
    "signature_bonus": (0.0, 20.0),
    "negative_evidence_penalty": (0.0, 25.0),
    "adjacency_threshold": (0.40, 0.70),
}


class LabeledExample(NamedTuple):
    """One labeled (profile, job) pair from the eval set.

    Attributes:
        profile_id: Which candidate profile this label is about.
        job_id: Which job.
        label: "fit", "maybe", or "not_fit" - only "fit" counts as
            relevant for precision@k, per the spec's plain precision@20/
            @50 framing (no partial credit described for "maybe").
        min_score: When set, this configuration is rejected unless this
            example's score is >= min_score - the recalibration target
            ("a Senior Backend/ML-Infra role... must land >=85") is one
            labeled example with min_score=85, not a rule hardcoded here.
        max_score: When set, rejected unless this example's score is <=
            max_score - the bidirectional regression constraints (a Data
            Scientist job must never exceed 40 for Dan's profile; a
            backend job must never exceed 40 for the frontend CV) are two
            labeled examples with max_score=40, the same way.
    """

    profile_id: str
    job_id: str
    label: str
    min_score: int | None = None
    max_score: int | None = None


class SearchResult(NamedTuple):
    config: ScoringConfig
    precision_at_20: float
    precision_at_50: float
    trials_evaluated: int
    trials_rejected: int


def _random_weights(rng: random.Random) -> WeightConfig:
    raw = {name: rng.uniform(*bounds) for name, bounds in WEIGHT_BOUNDS.items()}
    total = sum(raw.values())
    return WeightConfig(**{name: value / total for name, value in raw.items()})


def _random_family_fit(rng: random.Random) -> FamilyFitConfig:
    values = {name: rng.uniform(*bounds) for name, bounds in FAMILY_FIT_BOUNDS.items()}
    values["gate_cap"] = round(values["gate_cap"])
    return FamilyFitConfig(**values)


def random_config(rng: random.Random) -> ScoringConfig:
    """One randomly sampled configuration within WEIGHT_BOUNDS/FAMILY_FIT_BOUNDS."""
    return ScoringConfig(weights=_random_weights(rng), family_fit=_random_family_fit(rng))


def _precision_at_k(ranked_profile_examples: list[tuple[LabeledExample, int]], k: int) -> float:
    """Of the top k (by score) examples for one profile, the fraction
    labeled "fit". None (not a number) when the profile has fewer than k
    labeled examples at all - can't compute precision@k without k examples."""
    if len(ranked_profile_examples) < k:
        return None
    top_k = sorted(ranked_profile_examples, key=lambda pair: -pair[1])[:k]
    return sum(1 for example, _ in top_k if example.label == "fit") / k


def evaluate(
    examples: list[LabeledExample], score_fn: Callable[[LabeledExample, ScoringConfig], int], config: ScoringConfig,
) -> SearchResult | None:
    """Score every example under one configuration, check hard constraints,
    and compute precision@20/@50 averaged over every profile that has
    enough labeled examples for each.

    Args:
        examples: The labeled set.
        score_fn: (example, config) -> score, 0-100 - the CLI command
            supplies this, closed over each example's already-extracted
            CandidateProfile/JobRequirements so re-scoring under a new
            config doesn't re-parse CV/JD text on every trial.
        config: The configuration to evaluate.

    Returns:
        None when config violates a min_score/max_score constraint on any
        example; otherwise a SearchResult with trials_evaluated=
        trials_rejected=0 (the caller, run_search(), fills those in across
        the whole search).
    """
    scored = [(example, score_fn(example, config)) for example in examples]
    for example, value in scored:
        if example.min_score is not None and value < example.min_score:
            return None
        if example.max_score is not None and value > example.max_score:
            return None

    by_profile: dict[str, list[tuple[LabeledExample, int]]] = {}
    for pair in scored:
        by_profile.setdefault(pair[0].profile_id, []).append(pair)

    p20_values = [v for v in (_precision_at_k(pairs, 20) for pairs in by_profile.values()) if v is not None]
    p50_values = [v for v in (_precision_at_k(pairs, 50) for pairs in by_profile.values()) if v is not None]
    p20 = sum(p20_values) / len(p20_values) if p20_values else 0.0
    p50 = sum(p50_values) / len(p50_values) if p50_values else 0.0
    return SearchResult(config=config, precision_at_20=p20, precision_at_50=p50, trials_evaluated=0, trials_rejected=0)


def run_search(
    examples: list[LabeledExample], score_fn: Callable[[LabeledExample, ScoringConfig], int],
    trials: int = 500, seed: int = 0,
) -> SearchResult | None:
    """Random search over WEIGHT_BOUNDS/FAMILY_FIT_BOUNDS, scored by
    precision@20 + precision@50, keeping only the best configuration that
    violates no example's min_score/max_score.

    Args:
        examples: The labeled set.
        score_fn: See evaluate().
        trials: How many random configurations to try.
        seed: RNG seed - deterministic, not random.random(), so the same
            labeled set and trial count always finds the same winner.

    Returns:
        The best SearchResult, or None when every trial violated a
        constraint (the formula's shape is wrong, not just its weights -
        see this module's own docstring).
    """
    rng = random.Random(seed)
    best: SearchResult | None = None
    evaluated = rejected = 0
    for _ in range(trials):
        config = random_config(rng)
        result = evaluate(examples, score_fn, config)
        if result is None:
            rejected += 1
            continue
        evaluated += 1
        if best is None or (result.precision_at_20 + result.precision_at_50) > (best.precision_at_20 + best.precision_at_50):
            best = result
    if best is None:
        return None
    return best._replace(trials_evaluated=evaluated, trials_rejected=rejected)
