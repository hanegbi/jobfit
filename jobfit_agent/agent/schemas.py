"""Output schemas for every LLM call. Small local models drift without a strict schema."""

from typing import Literal

from pydantic import BaseModel, Field


# List caps are not cosmetic. An uncapped list is an invitation to a small model to
# keep writing, and one real run spent 20 minutes doing exactly that. The schema says
# how many items an answer needs; the token cap in config is the backstop.
class FitAnalysis(BaseModel):
    verdict: Literal["strong", "possible", "weak"]
    strengths: list[str] = Field(default_factory=list, max_length=6)
    gaps: list[str] = Field(default_factory=list, max_length=6)
    deal_breakers: list[str] = Field(default_factory=list, max_length=4)
    score_agreement: Literal["agrees", "higher", "lower"]  # your view vs the ATS score
    rationale: str = ""


class CvEdit(BaseModel):
    target: str          # the exact existing CV line this changes, or "new"
    change: str
    reason: str
    only_if_true: bool = False  # a new claim the candidate must verify before using


class CvPlan(BaseModel):
    summary: str = ""
    edits: list[CvEdit] = Field(default_factory=list, max_length=6)


class Critique(BaseModel):
    grounded: bool           # every edit quotes real CV text or is flagged only_if_true
    addresses_gaps: bool
    fabricated_claims: list[str] = Field(default_factory=list, max_length=6)
    feedback: str = ""


class Evidenced(BaseModel):
    evidence_urls: list[str] = Field(default_factory=list, max_length=8)  # urls of pages actually used


class FactsOut(Evidenced):
    employees: str | None = None
    location: str | None = None
    founded: str | None = None
    stage: str | None = None
    funding_total: str | None = None
    last_round: str | None = None


class ExitOut(Evidenced):
    outlook: Literal["ipo_likely", "acquisition_likely", "uncertain", "not_applicable", "no_data"]
    reasoning: str = ""
    signals: list[str] = Field(default_factory=list, max_length=5)


class Theme(BaseModel):
    text: str
    mentions: int = 1


class ReviewsOut(Evidenced):
    pros: list[Theme] = Field(default_factory=list, max_length=6)
    cons: list[Theme] = Field(default_factory=list, max_length=6)


class SalaryOut(Evidenced):
    role: str | None = None
    currency: str | None = None
    low: int | None = None
    high: int | None = None
    basis: Literal["base", "total", "unknown"] = "unknown"


class InterviewStage(BaseModel):
    stage: str
    questions: list[str] = Field(default_factory=list, max_length=8)


class InterviewOut(Evidenced):
    stages: list[InterviewStage] = Field(default_factory=list, max_length=6)
