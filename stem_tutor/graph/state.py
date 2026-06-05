from __future__ import annotations

from typing import Any, Optional, TypedDict

from stem_tutor.domain.models import (
    ErrorDiagnosis,
    FeedbackReport,
    ProblemInput,
    ReviewProblem,
    SolutionStep,
    VerificationResult,
)


class TutorGraphState(TypedDict, total=False):
    problem_input: ProblemInput
    raw_student_solution: str

    normalized_steps: list[SolutionStep]
    parse_warnings: list[str]
    ocr_meta: dict[str, Any]

    reference_solution: dict[str, Any]
    verification_results: list[VerificationResult]
    uncertainty_flags: list[str]

    diagnosis_results: list[ErrorDiagnosis]
    final_feedback: FeedbackReport
    review_problems: list[ReviewProblem]

    reference_computation_hints: str
    fail_reason: Optional[str]
    trace: list[str]
    run_meta: dict[str, Any]
    tool_calls_log: list[dict[str, Any]]

    budget_metadata: dict[str, Any]
    quality_signals: dict[str, Any]
    global_budget: dict[str, Any]
    subject_id: str
