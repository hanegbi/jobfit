"""Output schemas for every LLM call. Small local models drift without a strict schema."""

from typing import Literal

from pydantic import BaseModel, Field


class FitAnalysis(BaseModel):
    verdict: Literal["strong", "possible", "weak"]
    strengths: list[str]
    gaps: list[str]
    deal_breakers: list[str]
    score_agreement: Literal["agrees", "higher", "lower"]  # your view vs the ATS score
    rationale: str


class CvEdit(BaseModel):
    target: str          # the exact existing CV line this changes, or "new"
    change: str
    reason: str
    only_if_true: bool = False  # a new claim the candidate must verify before using


class CvPlan(BaseModel):
    summary: str
    edits: list[CvEdit]


class Critique(BaseModel):
    grounded: bool           # every edit quotes real CV text or is flagged only_if_true
    addresses_gaps: bool
    fabricated_claims: list[str]
    feedback: str


class Evidenced(BaseModel):
    evidence_urls: list[str] = Field(default_factory=list)  # urls of pages actually used


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
    signals: list[str] = Field(default_factory=list)


class Theme(BaseModel):
    text: str
    mentions: int = 1


class ReviewsOut(Evidenced):
    pros: list[Theme] = Field(default_factory=list)
    cons: list[Theme] = Field(default_factory=list)


class SalaryOut(Evidenced):
    role: str | None = None
    currency: str | None = None
    low: int | None = None
    high: int | None = None
    basis: Literal["base", "total", "unknown"] = "unknown"


class InterviewStage(BaseModel):
    stage: str
    questions: list[str]


class InterviewOut(Evidenced):
    stages: list[InterviewStage] = Field(default_factory=list)
