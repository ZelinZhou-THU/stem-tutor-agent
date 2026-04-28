from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from stem_tutor.subjects.loader import SubjectConfigModel, SubjectRegistry


@dataclass(frozen=True)
class TaxonomyEntry:
    code: str
    category: str
    short_desc: str
    cues: tuple[str, ...]


@dataclass(frozen=True)
class SubjectContext:
    subject_id: str
    display_name: str
    display_name_en: str
    error_taxonomy: dict[str, TaxonomyEntry]
    topic_keywords: dict[str, list[str]]
    prompts: dict[str, str]
    sympy_strip_prefixes: list[str]
    sympy_derivative_patterns: list[tuple[str, str]]
    rule_adjustments: list[dict[str, Any]]
    mock_reference_solution: dict[str, Any]
    mock_review_problems: list[dict[str, Any]]
    budget_overrides: dict[str, Any] | None = None

    @classmethod
    def from_config(cls, config: SubjectConfigModel) -> "SubjectContext":
        taxonomy = {}
        for code, entry in config.error_taxonomy.items():
            taxonomy[code] = TaxonomyEntry(
                code=code,
                category=entry.category,
                short_desc=entry.short_desc,
                cues=tuple(entry.cues),
            )

        prompts = {
            "system_role": config.prompts.system_role,
            "verification_role": config.prompts.verification_role,
            "verification_extra": config.prompts.verification_extra.strip(),
            "final_answer_role": config.prompts.final_answer_role,
            "final_answer_extra": config.prompts.final_answer_extra.strip(),
            "diagnosis_extra": config.prompts.diagnosis_extra.strip(),
            "feedback_extra": config.prompts.feedback_extra.strip(),
            "review_problem_extra": config.prompts.review_problem_extra.strip(),
            "review_problem_all_correct_extra": config.prompts.review_problem_all_correct_extra.strip(),
        }

        derivative_patterns = [
            (p.pattern, p.replacement)
            for p in config.sympy_postprocess.derivative_patterns
        ]

        rule_adjustments = [
            {
                "conditions": [c.model_dump() for c in ra.conditions],
                "label": ra.label,
                "evidence": ra.evidence,
                "violated_principles": ra.violated_principles,
            }
            for ra in config.rule_adjustments
        ]

        mock_review = [p.model_dump() for p in config.mock.review_problems]

        return cls(
            subject_id=config.subject_id,
            display_name=config.display_name,
            display_name_en=config.display_name_en,
            error_taxonomy=taxonomy,
            topic_keywords=config.topic_keywords,
            prompts=prompts,
            sympy_strip_prefixes=config.sympy_postprocess.strip_prefixes,
            sympy_derivative_patterns=derivative_patterns,
            rule_adjustments=rule_adjustments,
            mock_reference_solution=config.mock.reference_solution.model_dump(),
            mock_review_problems=mock_review,
            budget_overrides=config.budget_overrides,
        )


_context_cache: dict[str, SubjectContext] = {}


def get_subject_context(subject_id: str = "calculus") -> SubjectContext:
    if subject_id in _context_cache:
        return _context_cache[subject_id]
    config = SubjectRegistry.get(subject_id)
    if config is None:
        raise ValueError(f"Subject '{subject_id}' not found. Available: {SubjectRegistry.list_ids()}")
    ctx = SubjectContext.from_config(config)
    _context_cache[subject_id] = ctx
    return ctx


def list_subject_ids() -> list[str]:
    return SubjectRegistry.list_ids()
