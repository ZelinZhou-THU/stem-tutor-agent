from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from pydantic import ValidationError

from stem_tutor.domain.models import DiagnosisPayload, ErrorDiagnosis, VerificationLabel
from stem_tutor.graph.observability import record_provider_call
from stem_tutor.graph.state import TutorGraphState
from stem_tutor.prompts.templates import diagnosis_prompt
from stem_tutor.providers.base import LLMProvider
from stem_tutor.taxonomy.errors import lookup_error

MAX_DIAGNOSIS_WORKERS = 3


def _get_current_taxonomy(subject_id: str = "calculus") -> dict:
    from stem_tutor.taxonomy.errors import get_effective_taxonomy
    return get_effective_taxonomy(subject_id)


def _diagnose_single_step(
    step_text: str,
    step_id: str,
    evidence: str,
    allowed_codes: list[str],
    provider: LLMProvider,
    flags: list[str],
    run_meta: dict,
    problem_text: str = "",
    reference_solution: str = "",
    subject_id: str = "calculus",
) -> tuple[ErrorDiagnosis | None, list[str], dict]:
    from stem_tutor.prompts.templates import set_active_subject
    set_active_subject(subject_id)
    prompt = diagnosis_prompt(step_text, evidence, allowed_codes,
                               problem_text=problem_text, reference_solution=reference_solution)
    import time as _time
    _started_at = _time.perf_counter()
    raw = provider.diagnose_error(prompt)

    local_schema_fallback = False
    try:
        payload = DiagnosisPayload(**raw)
    except ValidationError:
        payload = DiagnosisPayload(
            error_code="NOTATION_UNCLEAR",
            root_cause_hypothesis="Provider returned invalid diagnosis schema.",
            supporting_evidence="Fell back to taxonomy-safe default diagnosis.",
            confidence=0.2,
        )
        local_schema_fallback = True

    sub_state: TutorGraphState = {
        "uncertainty_flags": flags,
        "run_meta": run_meta,
    }
    flags, run_meta = record_provider_call(
        sub_state,
        provider,
        node_name="diagnosis",
        fallback_flag="diagnosis_schema_fallback",
        local_schema_fallback=local_schema_fallback,
        started_at=_started_at,
    )

    from stem_tutor.taxonomy.errors import get_effective_taxonomy
    effective_taxonomy = get_effective_taxonomy(subject_id)
    if payload.error_code not in effective_taxonomy:
        if "diagnosis_unknown_error_code" not in flags:
            flags.append("diagnosis_unknown_error_code")
        payload = DiagnosisPayload(
            error_code="NOTATION_UNCLEAR",
            root_cause_hypothesis=payload.root_cause_hypothesis,
            supporting_evidence=payload.supporting_evidence,
            confidence=min(payload.confidence, 0.5),
        )

    entry = lookup_error(payload.error_code, subject_id=subject_id)
    category = entry.category if entry else "Unknown"
    diagnosis = ErrorDiagnosis(
        step_id=step_id,
        error_code=payload.error_code,
        category=category,
        root_cause_hypothesis=payload.root_cause_hypothesis,
        supporting_evidence=payload.supporting_evidence,
        confidence=payload.confidence,
    )
    return diagnosis, flags, run_meta


def make_diagnose_error_node(provider: LLMProvider):
    def diagnose_error_node(state: TutorGraphState) -> TutorGraphState:
        from stem_tutor.prompts.templates import set_active_subject
        subject_id = state.get("subject_id", "calculus")
        set_active_subject(subject_id)
        step_map = {s.step_id: s for s in state["normalized_steps"]}
        diagnoses: list[ErrorDiagnosis] = []
        taxonomy = _get_current_taxonomy(subject_id)
        allowed_codes = list(taxonomy.keys())
        flags = list(state.get("uncertainty_flags", []))
        run_meta = dict(state.get("run_meta", {}))

        problem_text = state["problem_input"].problem_text
        reference_text = state["reference_solution"].get("reference_text", "")

        incorrect_steps = []
        for v in state["verification_results"]:
            if v.label in (VerificationLabel.CORRECT, VerificationLabel.UNCLEAR):
                if v.label == VerificationLabel.UNCLEAR:
                    if "diagnosis_skip_unclear_step" not in flags:
                        flags.append("diagnosis_skip_unclear_step")
                continue
            step = step_map[v.step_id]
            incorrect_steps.append((step.normalized_text, v.step_id, v.evidence))

        if len(incorrect_steps) <= 1:
            for step_text, step_id, evidence in incorrect_steps:
                result, flags, run_meta = _diagnose_single_step(
                    step_text, step_id, evidence, allowed_codes, provider, flags, run_meta,
                    problem_text=problem_text, reference_solution=reference_text,
                    subject_id=subject_id,
                )
                if result is not None:
                    diagnoses.append(result)
        else:
            worker_count = min(MAX_DIAGNOSIS_WORKERS, len(incorrect_steps))
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = {}
                for step_text, step_id, evidence in incorrect_steps:
                    fut = executor.submit(
                        _diagnose_single_step,
                        step_text, step_id, evidence, allowed_codes, provider,
                        list(flags), dict(run_meta),
                        problem_text, reference_text, subject_id,
                    )
                    futures[fut] = step_id

                for future in as_completed(futures):
                    result, sub_flags, sub_meta = future.result()
                    if result is not None:
                        diagnoses.append(result)
                    flags.extend(sub_flags)
                    for k, v in sub_meta.get("node_stats", {}).items():
                        run_meta.setdefault("node_stats", {}).setdefault(k, {"provider_calls": 0, "fallback_calls": 0, "retry_sum": 0})
                        for mk, mv in v.items():
                            run_meta["node_stats"][k][mk] = run_meta["node_stats"][k].get(mk, 0) + mv
                    run_meta.setdefault("provider_events", []).extend(sub_meta.get("provider_events", []))

            logging.info(f"[diagnose_error] Parallel diagnosis completed for {len(incorrect_steps)} steps with {worker_count} workers")

        diagnoses.sort(key=lambda d: d.step_id)
        trace = state.get("trace", [])
        trace.append(f"diagnose_error: diagnosed {len(diagnoses)} steps")
        return {
            "diagnosis_results": diagnoses,
            "uncertainty_flags": flags,
            "trace": trace,
            "run_meta": run_meta,
        }

    return diagnose_error_node
