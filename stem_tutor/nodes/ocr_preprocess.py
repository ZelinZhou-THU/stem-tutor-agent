from __future__ import annotations

from stem_tutor.graph.observability import record_provider_call
from stem_tutor.graph.state import TutorGraphState
from stem_tutor.providers.base import LLMProvider


def make_ocr_preprocess_node(provider: LLMProvider):
    def ocr_preprocess_node(state: TutorGraphState) -> TutorGraphState:
        from stem_tutor.prompts.templates import set_active_subject
        subject_id = state.get("subject_id", "calculus")
        set_active_subject(subject_id)
        problem = state["problem_input"]
        flags = list(state.get("uncertainty_flags", []))
        warnings = list(state.get("parse_warnings", []))

        if problem.source_type != "ocr":
            trace = state.get("trace", [])
            trace.append("ocr_preprocess: skipped")
            return {
                "trace": trace,
                "uncertainty_flags": flags,
                "parse_warnings": warnings,
            }

        if not problem.ocr_payload:
            flags.append("ocr_missing_payload")
            trace = state.get("trace", [])
            trace.append("ocr_preprocess: missing payload")
            return {
                "trace": trace,
                "uncertainty_flags": flags,
                "parse_warnings": warnings,
                "fail_reason": "source_type is ocr but ocr_payload is empty",
            }

        out = provider.ocr_to_text(problem.ocr_payload)
        text = str(out.get("text", "")).strip()
        quality_score = float(out.get("quality_score", 0.5))
        ocr_warnings = list(out.get("warnings", []))
        formula_format = str(out.get("formula_format", "latex_like"))

        if quality_score < 0.7:
            flags.append("ocr_low_quality")
        flags.append("ocr_source_input")
        warnings.extend([f"ocr_warning:{w}" for w in ocr_warnings])

        sub_state: TutorGraphState = {
            "uncertainty_flags": flags,
            "run_meta": dict(state.get("run_meta", {})),
        }
        flags, run_meta = record_provider_call(
            sub_state,
            provider,
            node_name="ocr",
            fallback_flag="ocr_fallback",
            local_schema_fallback=(not text),
        )

        trace = state.get("trace", [])
        trace.append("ocr_preprocess: ocr extracted text")

        return {
            "raw_student_solution": text or state.get("raw_student_solution", ""),
            "ocr_meta": {
                "quality_score": quality_score,
                "warnings": ocr_warnings,
                "formula_format": formula_format,
            },
            "uncertainty_flags": flags,
            "parse_warnings": warnings,
            "trace": trace,
            "run_meta": run_meta,
        }

    return ocr_preprocess_node
