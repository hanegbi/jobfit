"""Single configuration object for sub-score weights, hard gates, and band
thresholds, so tuning the scorer never means hunting for scattered
constants across modules.
"""

from pydantic import BaseModel, Field


class GateConfig(BaseModel):
    """Score ceilings applied after the weighted sum, in order.

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
        role_family_mismatch_cap: Ceiling when none of the candidate's
            recent roles share the job's role family.
    """

    unmet_hard_requirement_cap: int = 45
    missing_skills_cap: int = 55
    missing_skills_threshold: int = 2
    seniority_gap_cap: int = 50
    seniority_gap_threshold: int = 2
    role_family_mismatch_cap: int = 40


class WeightConfig(BaseModel):
    """Weight of each sub-score in the final weighted sum. Must sum to 1.0.

    Attributes:
        must_have_coverage: Weight of must-have requirement coverage.
        title_and_seniority_fit: Weight of title/seniority/role-family fit.
        experience_relevance: Weight of recent-experience domain relevance.
        nice_to_have_coverage: Weight of nice-to-have requirement coverage.
        evidence_depth: Weight of scale/ownership/outcome evidence in the CV.
    """

    must_have_coverage: float = 0.40
    title_and_seniority_fit: float = 0.20
    experience_relevance: float = 0.20
    nice_to_have_coverage: float = 0.10
    evidence_depth: float = 0.10

    def validate_sums_to_one(self) -> None:
        """Raise if the weights do not sum to 1.0 (within floating tolerance).

        Raises:
            ValueError: If the weights do not sum to 1.0.
        """
        total = (
            self.must_have_coverage + self.title_and_seniority_fit
            + self.experience_relevance + self.nice_to_have_coverage
            + self.evidence_depth
        )
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


class ScoringConfig(BaseModel):
    """Top-level configuration bundle passed through the scorer.

    Attributes:
        weights: Sub-score weights.
        gates: Hard-gate thresholds and caps.
        bands: Score-band boundaries.
        match_strength_weights: Coverage weight per match strength.
        experience: experience_relevance tuning.
        stale_skill_years: A skill last used more than this many years ago
            counts as stale (role evidence downgrades from strong to
            medium match strength).
    """

    weights: WeightConfig = Field(default_factory=WeightConfig)
    gates: GateConfig = Field(default_factory=GateConfig)
    bands: BandConfig = Field(default_factory=BandConfig)
    match_strength_weights: MatchStrengthWeights = Field(default_factory=MatchStrengthWeights)
    experience: ExperienceRelevanceConfig = Field(default_factory=ExperienceRelevanceConfig)
    stale_skill_years: int = 3

    def model_post_init(self, __context) -> None:
        """Validate the weight configuration immediately after construction.

        Args:
            __context: Pydantic-supplied post-init context (unused).
        """
        self.weights.validate_sums_to_one()


DEFAULT_CONFIG = ScoringConfig()
