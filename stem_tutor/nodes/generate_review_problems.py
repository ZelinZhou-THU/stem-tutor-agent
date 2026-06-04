from __future__ import annotations

import logging

from pydantic import ValidationError

from stem_tutor.domain.models import ReviewProblem, ReviewProblemsPayload
from stem_tutor.graph.observability import record_provider_call
from stem_tutor.graph.state import TutorGraphState
from stem_tutor.prompts.templates import review_problem_prompt
from stem_tutor.providers.base import LLMProvider
from stem_tutor.subjects.context import get_subject_context
from stem_tutor.taxonomy.errors import get_effective_taxonomy

_log = logging.getLogger(__name__)


def _get_topic_keywords(subject_id: str = "calculus") -> dict[str, list[str]]:
    try:
        ctx = get_subject_context(subject_id)
        return ctx.topic_keywords
    except Exception:
        return {}


def _infer_topic_tags(problem_text: str, reference_text: str, subject_id: str = "calculus") -> list[str]:
    combined = (problem_text + " " + reference_text).lower()
    tags = []
    topic_keywords = _get_topic_keywords(subject_id)
    for tag, keywords in topic_keywords.items():
        if any(kw in combined for kw in keywords):
            tags.append(tag)
    return tags[:3]


def make_generate_review_problems_node(provider: LLMProvider):
    def generate_review_problems_node(state: TutorGraphState) -> TutorGraphState:
        from stem_tutor.prompts.templates import set_active_subject
        subject_id = state.get("subject_id", "calculus")
        set_active_subject(subject_id)
        try:
            return _generate_review_problems_inner(state, provider)
        except Exception as exc:
            _log.error(
                "[Review] Failed to generate review problems: %s: %s",
                type(exc).__name__, exc,
            )
            trace = state.get("trace", [])
            trace.append(f"generate_review_problems: skipped due to {type(exc).__name__}")
            return {
                "review_problems": [],
                "uncertainty_flags": list(state.get("uncertainty_flags", [])),
                "trace": trace,
                "run_meta": dict(state.get("run_meta", {})),
            }

    return generate_review_problems_node


def _generate_review_problems_inner(state: TutorGraphState, provider: LLMProvider) -> TutorGraphState:
    diagnoses = state.get("diagnosis_results", [])
    weakness_codes = list({d.error_code for d in diagnoses})[:3]
    topic_tags = list(state["problem_input"].topic_tags)

    if not topic_tags:
        problem_text = state["problem_input"].problem_text
        ref_solution = state.get("reference_solution", {})
        ref_text = ref_solution.get("reference_text", "") if ref_solution else ""
        subject_id = state.get("subject_id", "calculus")
        topic_tags = _infer_topic_tags(problem_text, ref_text, subject_id)

    verification_results = state.get("verification_results", [])
    all_correct = all(v.label.value == "correct" for v in verification_results) if verification_results else False

    problem_text = state["problem_input"].problem_text
    prompt = review_problem_prompt(weakness_codes, topic_tags, all_correct=all_correct, problem_text=problem_text)
    import time as _time
    _started_at = _time.perf_counter()
    raw = provider.generate_review_problems(prompt)
    flags = list(state.get("uncertainty_flags", []))
    run_meta = dict(state.get("run_meta", {}))

    local_schema_fallback = False
    try:
        payload = ReviewProblemsPayload(**raw)
        problems = payload.problems[:3]
    except ValidationError:
        problems = []
        local_schema_fallback = True

    sub_state: TutorGraphState = {
        "uncertainty_flags": flags,
        "run_meta": run_meta,
    }
    flags, run_meta = record_provider_call(
        sub_state,
        provider,
        node_name="review",
        fallback_flag="review_schema_fallback",
        local_schema_fallback=local_schema_fallback,
        started_at=_started_at,
    )

    safe_problems: list[ReviewProblem] = []
    subject_id = state.get("subject_id", "calculus")
    effective_taxonomy = get_effective_taxonomy(subject_id)
    for p in problems:
        code = p.related_weakness_code
        if code not in effective_taxonomy:
            if "review_unknown_weakness_code" not in flags:
                flags.append("review_unknown_weakness_code")
            code = "NOTATION_UNCLEAR"
        safe_problems.append(
            ReviewProblem(
                problem_text=p.problem_text,
                related_weakness_code=code,
                rationale=p.rationale,
                difficulty_label=p.difficulty_label,
            )
        )

    trace = state.get("trace", [])
    trace.append(f"generate_review_problems: generated {len(safe_problems)} problems")
    return {
        "review_problems": safe_problems,
        "uncertainty_flags": flags,
        "trace": trace,
        "run_meta": run_meta,
    }
