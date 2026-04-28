from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


class VerificationLabel(str, Enum):
    CORRECT = "correct"
    INCORRECT_MATH = "incorrect_math"
    INCONSISTENT_OR_UNSUPPORTED = "inconsistent_or_unsupported"
    UNCLEAR = "unclear"


class ProblemInput(BaseModel):
    problem_id: str = Field(min_length=1)
    problem_text: str = Field(min_length=1)
    source_type: Literal["text", "ocr"] = Field(default="text")
    ocr_payload: Optional[str] = None
    topic_tags: list[str] = Field(default_factory=list)
    expected_format: str = Field(default="text")
    difficulty_hint: Optional[str] = None


class SolutionStep(BaseModel):
    step_id: str = Field(min_length=1)
    raw_text: str = Field(min_length=1)
    normalized_text: str = Field(min_length=1)
    extracted_expression: Optional[str] = None
    depends_on_step_ids: list[str] = Field(default_factory=list)


class VerificationResult(BaseModel):
    step_id: str
    label: VerificationLabel
    evidence: str
    confidence: float = Field(ge=0.0, le=1.0)
    violated_principles: list[str] = Field(default_factory=list)
    sympy_verified: bool = False
    sympy_equivalent: Optional[bool] = None


class ErrorDiagnosis(BaseModel):
    step_id: str
    error_code: str
    category: str
    root_cause_hypothesis: str
    supporting_evidence: str
    confidence: float = Field(ge=0.0, le=1.0)


class FeedbackReport(BaseModel):
    first_critical_step_id: Optional[str] = None
    concise_summary: str
    likely_cause: Optional[str] = None
    review_concepts: list[str] = Field(default_factory=list)
    next_action: str
    caution_note: Optional[str] = None


class ReviewProblem(BaseModel):
    problem_text: str
    related_weakness_code: str
    rationale: str
    difficulty_label: Optional[str] = None


class ReferenceSolutionPayload(BaseModel):
    reference_text: str = Field(min_length=1)
    key_assertions: list[str] = Field(default_factory=list)


class VerificationPayload(BaseModel):
    label: VerificationLabel
    evidence: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    violated_principles: list[str] = Field(default_factory=list)
    sympy_verified: bool = False
    sympy_equivalent: Optional[bool] = None


class DiagnosisPayload(BaseModel):
    error_code: str = Field(min_length=1)
    root_cause_hypothesis: str = Field(min_length=1)
    supporting_evidence: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


class FeedbackPayload(BaseModel):
    concise_summary: str = Field(min_length=1)
    next_action: str = Field(min_length=1)
    caution_note: str = Field(default="")


class ReviewProblemsPayload(BaseModel):
    problems: list[ReviewProblem] = Field(default_factory=list)
