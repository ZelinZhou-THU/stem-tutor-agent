from __future__ import annotations

from pydantic import ValidationError

from stem_tutor.domain.models import FeedbackPayload, FeedbackReport
from stem_tutor.graph.observability import record_provider_call
from stem_tutor.graph.state import TutorGraphState
from stem_tutor.prompts.templates import feedback_prompt
from stem_tutor.providers.base import LLMProvider


def make_generate_feedback_node(provider: LLMProvider):
    def generate_feedback_node(state: TutorGraphState) -> TutorGraphState:
        diagnoses = state.get("diagnosis_results", [])
        first_error = diagnoses[0] if diagnoses else None
        concepts = list({d.error_code for d in diagnoses})[:3]
        problem_input = state.get("problem_input")
        problem_text = problem_input.problem_text if problem_input else ""

        prompt = feedback_prompt(
            first_error.step_id if first_error else None,
            first_error.root_cause_hypothesis if first_error else None,
            concepts,
            problem_text=problem_text,
        )
        import time as _time
        _started_at = _time.perf_counter()
        raw = provider.generate_feedback(prompt)
        flags = list(state.get("uncertainty_flags", []))
        run_meta = dict(state.get("run_meta", {}))

        local_schema_fallback = False
        try:
            payload = FeedbackPayload(**raw)
        except ValidationError:
            payload = FeedbackPayload(
                concise_summary="Feedback generation was unstable. Please review the first flagged step.",
                next_action="Rewrite the first incorrect step and justify each transformation with one rule.",
                caution_note="Fallback feedback used due to schema validation failure.",
            )
            local_schema_fallback = True

        sub_state: TutorGraphState = {
            "uncertainty_flags": flags,
            "run_meta": run_meta,
        }
        flags, run_meta = record_provider_call(
            sub_state,
            provider,
            node_name="feedback",
            fallback_flag="feedback_schema_fallback",
            local_schema_fallback=local_schema_fallback,
            started_at=_started_at,
        )

        report = FeedbackReport(
            first_critical_step_id=first_error.step_id if first_error else None,
            concise_summary=payload.concise_summary,
            likely_cause=first_error.root_cause_hypothesis if first_error else None,
            review_concepts=concepts,
            next_action=payload.next_action,
            caution_note=payload.caution_note,
        )

        trace = state.get("trace", [])
        trace.append("generate_feedback: feedback prepared")
        return {
            "final_feedback": report,
            "uncertainty_flags": flags,
            "trace": trace,
            "run_meta": run_meta,
        }

    return generate_feedback_node
