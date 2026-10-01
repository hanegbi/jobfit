"""Pydantic models shared across extraction, matching, and scoring."""

from enum import Enum

from pydantic import BaseModel, Field


class RequirementKind(str, Enum):
    """The category of a single job requirement."""

    SKILL = "skill"
    YEARS = "years"
    DOMAIN = "domain"
    DEGREE = "degree"
    LANGUAGE = "language"
    LOCATION = "location"
    CERTIFICATION = "certification"


class Seniority(str, Enum):
    """Career-level bucket, ordered from least to most senior for gap math."""

    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"
    STAFF = "staff"
    LEAD = "lead"
    PRINCIPAL = "principal"
    MANAGER = "manager"
    HEAD = "head"


SENIORITY_LEVEL: dict[Seniority, int] = {
    Seniority.JUNIOR: 0,
    Seniority.MID: 1,
    Seniority.SENIOR: 2,
    Seniority.STAFF: 3,
    Seniority.LEAD: 3,
    Seniority.PRINCIPAL: 4,
    Seniority.MANAGER: 3,
    Seniority.HEAD: 5,
}
"""Numeric level per Seniority, used only to measure gaps - staff/lead sit
at the same level since they're lateral senior-IC titles, not a strict
hierarchy step apart."""


class MatchStrength(str, Enum):
    """How well a single requirement is backed by CV evidence."""

    STRONG = "strong"
    MEDIUM = "medium"
    WEAK = "weak"
    NONE = "none"


class Requirement(BaseModel):
    """A single requirement extracted from a job description.

    Attributes:
        kind: The requirement's category.
        text: The raw bullet or clause text it came from.
        canonical: The canonical skill/entity name, when kind is SKILL;
            None otherwise.
        years: Years of experience attached to this specific requirement,
            when stated (e.g. "5+ years of Python").
    """

    kind: RequirementKind
    text: str
    canonical: str | None = None
    years: int | None = None


class JobRequirements(BaseModel):
    """Structured requirements extracted from one job description.

    Attributes:
        title: The job's stated title.
        seniority: The job's inferred seniority level.
        must_have: Requirements classified as mandatory.
        nice_to_have: Requirements classified as optional/bonus.
        required_years_total: Overall years-of-experience requirement not
            attached to a specific skill, when stated.
        domain: The inferred business/technical domain (e.g. "fintech"),
            or None if not determinable.
        role_family: The inferred role family (e.g. "backend"), or None.
    """

    title: str
    seniority: Seniority
    must_have: list[Requirement] = Field(default_factory=list)
    nice_to_have: list[Requirement] = Field(default_factory=list)
    required_years_total: int | None = None
    domain: str | None = None
    role_family: str | None = None


class SkillEvidence(BaseModel):
    """One skill found in a CV, with where and how strongly it's backed.

    Attributes:
        canonical: The canonical skill name.
        evidence_strength: "strong" when backed by a role bullet, "weak"
            when only found in a skills-list section.
        years: Years the skill was used in the role it was found in, when
            derivable from that role's dates; None otherwise.
        recency_years: Years since the skill was last used (0 for a
            current/present role); None if undeterminable.
    """

    canonical: str
    evidence_strength: str
    years: int | None = None
    recency_years: int | None = None


class Role(BaseModel):
    """One employment entry parsed from a CV.

    Attributes:
        title: The role's job title.
        company: The employer name, when parsed; otherwise None.
        start: Start date as a "YYYY" or "YYYY-MM" string, when parsed.
        end: End date in the same format, or "present" for an ongoing role.
        bullets: The role's bullet-point description lines.
        family: The role's inferred role family, or None.
    """

    title: str
    company: str | None = None
    start: str | None = None
    end: str | None = None
    bullets: list[str] = Field(default_factory=list)
    family: str | None = None


class CandidateProfile(BaseModel):
    """Structured profile extracted from one CV.

    Attributes:
        roles: Employment history, most recent first.
        skills: Every skill found, with its evidence.
        domains: Business/technical domains the candidate has worked in.
        education: Education entries found in the CV.
        certifications: Certification entries found in the CV.
        languages: Spoken/written languages found in the CV.
        location: The candidate's stated location, or None.
        seniority: The candidate's inferred overall seniority level.
        family_affinity: Recency/duration-weighted fraction of the
            candidate's work history in each role family, over every
            known family (0.0 where none), summing to 1.0 across families
            with any weighted experience - see ats_scorer/profile.py.
            Empty until build_profile() fills it in;
            extract_candidate_profile() alone leaves it empty.
        signature_skills: Canonical skill names backed by real role-bullet
            evidence whose IDF clears the signature bar - see
            ats_scorer/profile.py. Same empty-until-build_profile() note.
    """

    roles: list[Role] = Field(default_factory=list)
    skills: list[SkillEvidence] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    location: str | None = None
    seniority: Seniority = Seniority.MID
    family_affinity: dict[str, float] = Field(default_factory=dict)
    signature_skills: list[str] = Field(default_factory=list)


class MatchedRequirement(BaseModel):
    """One job requirement paired with how well the CV backs it.

    Attributes:
        requirement: The requirement being matched.
        strength: How strongly the CV backs it.
        evidence: A short human-readable description of the supporting
            evidence, or None when strength is NONE.
    """

    requirement: Requirement
    strength: MatchStrength
    evidence: str | None = None


class MatchResult(BaseModel):
    """The full result of matching one CandidateProfile against one
    JobRequirements.

    Attributes:
        must_have_matches: Every must_have requirement paired with its
            match strength.
        nice_to_have_matches: Every nice_to_have requirement paired with
            its match strength.
        role_family_match: Whether the candidate's recent role family
            matches the job's role family.
        seniority_gap: Signed level gap (candidate level minus job level;
            positive means the candidate is more senior).
        recent_relevant_fraction: Fraction of the candidate's recent
            (experience.recent_years_window) work judged relevant to the
            job's domain/role family, in [0.0, 1.0].
    """

    must_have_matches: list[MatchedRequirement] = Field(default_factory=list)
    nice_to_have_matches: list[MatchedRequirement] = Field(default_factory=list)
    role_family_match: bool = False
    seniority_gap: int = 0
    recent_relevant_fraction: float = 0.0


class SubScore(BaseModel):
    """One weighted component of the final score.

    Attributes:
        score: The sub-score, 0 to 100.
        weight: This sub-score's weight in the final weighted sum.
        reasons: Short, human-readable reasons behind this sub-score.
    """

    score: int
    weight: float
    reasons: list[str] = Field(default_factory=list)


class ScoreResult(BaseModel):
    """The final scoring output.

    Attributes:
        score: The final integer score, 0 to 100 - family_fit x job_fit,
            gated.
        band: The fixed-meaning band name the score falls into.
        family_fit: The family_fit factor itself, 0 to 1 - see
            ats_scorer/family_fit.py. Exposed separately from the score so
            a reviewer (or fit-weights) can tell "wrong family" apart from
            "right family, weak requirement match" at a glance.
        sub_scores: Every named job_fit sub-score that fed its weighted sum.
        gates_applied: Names of hard gates that capped the score, if any -
            "family_fit_low" is one of them, applied after the multiply.
        matched_must_haves: Must-have requirements with strength other
            than NONE, each as {requirement, evidence, strength}.
        missing_must_haves: Must-have requirement texts with strength NONE.
        matched_nice_to_haves: Nice-to-have requirements with strength
            other than NONE, in the same shape as matched_must_haves.
        score_adjustments: Human-readable notes for the signature bonus and
            negative-evidence penalty, when either applied - additive/
            subtractive on job_fit, not part of sub_scores' weighted sum.
        top_gaps: Up to three gap descriptions, ordered by impact on score.
        summary: A two-sentence, plain-language summary.
    """

    score: int
    band: str
    family_fit: float = 0.0
    sub_scores: dict[str, SubScore]
    gates_applied: list[str] = Field(default_factory=list)
    matched_must_haves: list[dict] = Field(default_factory=list)
    missing_must_haves: list[str] = Field(default_factory=list)
    matched_nice_to_haves: list[dict] = Field(default_factory=list)
    score_adjustments: list[str] = Field(default_factory=list)
    top_gaps: list[str] = Field(default_factory=list)
    summary: str = ""
