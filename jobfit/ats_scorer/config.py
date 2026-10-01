"""Single configuration object for sub-score weights, hard gates, and band
thresholds, so tuning the scorer never means hunting for scattered
constants across modules.
"""

import json
from pathlib import Path

from pydantic import BaseModel, Field

# Where `ats_scorer.cli fit-weights` writes its winning WeightConfig/
# FamilyFitConfig - same pattern as skill_idf.json/family_adjacency.json:
# a reviewable, committed data file rather than regenerated Python source.
# Absent until fit-weights has run once against a real labeled set.
FIT_WEIGHTS_RESULT_PATH = Path(__file__).parent / "data" / "fit_weights.json"


class GateConfig(BaseModel):
    """Score ceilings applied to job_fit, before the family_fit multiply.

    Attributes:
        unmet_hard_requirement_cap: Ceiling when a must_have of kind years,
            degree, certification, language, or location is explicitly
            unmet.
        missing_skills_cap: Ceiling when two or more must_have skills have
            match strength none.
        missing_skills_threshold: Number of none-strength must_have skills
            that triggers missing_skills_cap.
        seniority_gap_cap: Ceiling when the seniority gap is two or more
            levels in either direction.
        seniority_gap_threshold: Absolute level gap that triggers
            seniority_gap_cap.

    role_family_mismatch_cap is gone - family_fit (see FamilyFitConfig) is
    its replacement: a continuous, adjacency-aware multiplier applied to
    the whole score rather than a flat cap on job_fit alone.
    """

    unmet_hard_requirement_cap: int = 45
    missing_skills_cap: int = 55
    missing_skills_threshold: int = 2
    seniority_gap_cap: int = 50
    seniority_gap_threshold: int = 2


class WeightConfig(BaseModel):
    """Weight of each job_fit sub-score in its weighted sum. Must sum to 1.0.

    Attributes:
        must_have_coverage: Weight of IDF-weighted must-have coverage.
        seniority_fit: Weight of seniority-gap fit.
        nice_to_have_coverage: Weight of IDF-weighted nice-to-have coverage.
        evidence_depth: Weight of scale/ownership/outcome evidence in the CV.

    experience_relevance is gone - it measured the same thing family_fit now
    does (is recent experience in the job's family), more crudely (last 2
    roles, binary match) than family_fit's full weighted vector. Keeping
    both would double-count the same signal twice. Placeholder weights,
    pending `fit-weights` - see FamilyFitConfig.
    """

    must_have_coverage: float = 0.50
    seniority_fit: float = 0.25
    nice_to_have_coverage: float = 0.125
    evidence_depth: float = 0.125

    def validate_sums_to_one(self) -> None:
        """Raise if the weights do not sum to 1.0 (within floating tolerance).

        Raises:
            ValueError: If the weights do not sum to 1.0.
        """
        total = self.must_have_coverage + self.seniority_fit + self.nice_to_have_coverage + self.evidence_depth
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"WeightConfig weights must sum to 1.0, got {total}")


class BandConfig(BaseModel):
    """Fixed-meaning score bands, each a (low, high) inclusive integer range.

    Attributes:
        strong_match: Range for "strong match".
        good_match: Range for "good match".
        partial_match: Range for "partial match".
        weak_match: Range for "weak match".
        not_a_fit: Range for "not a fit".
    """

    strong_match: tuple[int, int] = (85, 100)
    good_match: tuple[int, int] = (70, 84)
    partial_match: tuple[int, int] = (50, 69)
    weak_match: tuple[int, int] = (30, 49)
    not_a_fit: tuple[int, int] = (0, 29)

    def band_for(self, score: int) -> str:
        """Return the band name for a given integer score.

        Args:
            score: Integer score in [0, 100].

        Returns:
            The band's human-readable name.

        Raises:
            ValueError: If score falls outside every configured band.
        """
        bands = [
            ("strong match", self.strong_match),
            ("good match", self.good_match),
            ("partial match", self.partial_match),
            ("weak match", self.weak_match),
            ("not a fit", self.not_a_fit),
        ]
        for name, (low, high) in bands:
            if low <= score <= high:
                return name
        raise ValueError(f"score {score} falls outside every configured band")


class MatchStrengthWeights(BaseModel):
    """Numeric weight applied per match strength when computing coverage.

    Attributes:
        strong: Weight for a strong match.
        medium: Weight for a medium match.
        weak: Weight for a weak match.
        none: Weight for no match.
    """

    strong: float = 1.0
    medium: float = 0.7
    weak: float = 0.3
    none: float = 0.0


class ExperienceRelevanceConfig(BaseModel):
    """Tuning for the experience_relevance sub-score.

    Attributes:
        recent_years_window: How many recent years of work count as
            "recent" for domain-relevance purposes.
        full_credit_fraction: Fraction of recent work in the same domain
            required for full credit.
        adjacent_domain_credit: Credit fraction given for adjacent-domain
            (same role family, different declared domain) recent work.
    """

    recent_years_window: int = 5
    full_credit_fraction: float = 0.6
    adjacent_domain_credit: float = 0.5


class ProfileConfig(BaseModel):
    """Tuning for build_profile()'s family-affinity vector and signature
    skills (see ats_scorer/profile.py).

    Attributes:
        recency_decay_years: A role's weight in the family-affinity vector
            decays linearly to 0 over this many years since it ended; a
            current role (0 years ago) carries full weight.
        signature_idf_threshold: A CV skill counts as "signature" only when
            backed by role-bullet evidence (not just listed) AND its IDF is
            at or above this. 6.0 sits at roughly the 80th percentile of
            the real skill_idf.json distribution and is at or below every
            one of the rare skills the user named as their own signature
            (Model Inference 6.71, GPU Programming 6.23, Model Quantization
            6.63, ONNX/LLM Evaluation 7.08, Distributed Inference 7.89,
            Triton 8.58) while excluding common/moderate skills.
    """

    recency_decay_years: float = 6.0
    signature_idf_threshold: float = 6.0


class FamilyFitConfig(BaseModel):
    """score = family_fit x job_fit - every coefficient here is a free
    parameter `fit-weights` searches over (see section 5 of the design
    spec); these are seed values, not a tuned result.

    Attributes:
        gate_threshold: family_fit below this caps the final score at
            gate_cap, applied after the multiply - "exactly as specified"
            in the spec's own worked example (0.3).
        gate_cap: The cap a low family_fit applies (35, per the spec).
        signature_bonus: Added to job_fit (not folded into coverage) when
            2+ of the job's own requirements are also the candidate's
            signature skills. Spec's stated search bound is [0, 20]; 10.0
            is the unfit midpoint, not a chosen value.
        negative_evidence_penalty: Per matched must-have whose own text
            classifies into a family that isn't the job's family or
            adjacent to it (adjacency >= adjacency_threshold), scaled by
            that match's strength (strong=1.0, medium=0.7, weak=0.3, from
            match_strength_weights). Placeholder pending fit-weights.
        adjacency_threshold: Two families count as "adjacent" (so a
            must-have classified into one doesn't trigger the negative-
            evidence penalty against the other) when family_adjacency.json
            reports at least this much cosine similarity between them.
    """

    gate_threshold: float = 0.3
    gate_cap: int = 35
    signature_bonus: float = 10.0
    negative_evidence_penalty: float = 15.0
    adjacency_threshold: float = 0.5


class ScoringConfig(BaseModel):
    """Top-level configuration bundle passed through the scorer.

    Attributes:
        weights: Sub-score weights.
        gates: Hard-gate thresholds and caps.
        bands: Score-band boundaries.
        match_strength_weights: Coverage weight per match strength.
        experience: experience_relevance tuning (still used by matcher.py's
            recent_relevant_fraction, which MatchResult still carries even
            though scorer.py's weighted sum no longer reads it).
        profile: family-affinity/signature-skill tuning.
        family_fit: family_fit gate and bonus/penalty coefficients.
        stale_skill_years: A skill last used more than this many years ago
            counts as stale (role evidence downgrades from strong to
            medium match strength).
    """

    weights: WeightConfig = Field(default_factory=WeightConfig)
    gates: GateConfig = Field(default_factory=GateConfig)
    bands: BandConfig = Field(default_factory=BandConfig)
    match_strength_weights: MatchStrengthWeights = Field(default_factory=MatchStrengthWeights)
    experience: ExperienceRelevanceConfig = Field(default_factory=ExperienceRelevanceConfig)
    profile: ProfileConfig = Field(default_factory=ProfileConfig)
    family_fit: FamilyFitConfig = Field(default_factory=FamilyFitConfig)
    stale_skill_years: int = 3

    def model_post_init(self, __context) -> None:
        """Validate the weight configuration immediately after construction.

        Args:
            __context: Pydantic-supplied post-init context (unused).
        """
        self.weights.validate_sums_to_one()


def _load_default_config() -> ScoringConfig:
    """ScoringConfig() seed values, overridden by fit_weights.json's
    "weights"/"family_fit" blocks when that file exists - the search's
    winning configuration, not a number anyone chose by eye."""
    if not FIT_WEIGHTS_RESULT_PATH.exists():
        return ScoringConfig()
    tuned = json.loads(FIT_WEIGHTS_RESULT_PATH.read_text(encoding="utf-8"))
    return ScoringConfig(
        weights=WeightConfig(**tuned["weights"]),
        family_fit=FamilyFitConfig(**tuned["family_fit"]),
    )


DEFAULT_CONFIG = _load_default_config()
