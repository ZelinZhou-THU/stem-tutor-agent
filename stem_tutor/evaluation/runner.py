from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any

from stem_tutor.domain.models import ProblemInput
from stem_tutor.evaluation.baseline import run_single_prompt_baseline
from stem_tutor.graph.workflow import run_tutor_graph
from stem_tutor.taxonomy.errors import lookup_error


def _verification_accuracy(pred: list[dict[str, Any]], gold: list[dict[str, Any]]) -> float:
    if not gold:
        return 1.0
    gold_map = {g["step_id"]: g["label"] for g in gold}
    pred_map = {p["step_id"]: p["label"] for p in pred}

    total = 0
    hit = 0
    for step_id, gold_label in gold_map.items():
        if step_id not in pred_map:
            continue
        total += 1
        if pred_map[step_id] == gold_label:
            hit += 1
    return hit / total if total else 0.0


def _diagnosis_hit(pred: list[dict[str, Any]], gold_codes: list[str]) -> float:
    if not gold_codes:
        return 1.0
    pred_codes = {p.get("error_code", "") for p in pred}
    overlap = len(pred_codes.intersection(set(gold_codes)))
    return overlap / len(set(gold_codes))


def _first_error_step(pred_verify: list[dict[str, Any]]) -> str | None:
    order = sorted(pred_verify, key=lambda x: x.get("step_id", ""))
    for item in order:
        label = item.get("label", "")
        if label != "correct":
            return item.get("step_id")
    return None


def _feedback_proxy(feedback: dict[str, Any], expected_step: str | None) -> float:
    score = 0.0
    if feedback.get("next_action"):
        score += 0.5
    first = feedback.get("first_critical_step_id")
    if expected_step is None:
        score += 0.5
    elif first == expected_step:
        score += 0.5
    return score


def _review_relevance_proxy(review_problems: list[dict[str, Any]], gold_codes: list[str]) -> float:
    if not gold_codes:
        return 1.0
    if not review_problems:
        return 0.0
    pred_codes = {p.get("related_weakness_code", "") for p in review_problems}
    overlap = len(pred_codes.intersection(set(gold_codes)))
    return overlap / len(set(gold_codes))


def _error_step_recall(pred: list[dict[str, Any]], gold: list[dict[str, Any]]) -> float:
    if not gold:
        return 1.0
    gold_error_steps = {g["step_id"] for g in gold if g.get("label") != "correct"}
    if not gold_error_steps:
        return 1.0
    pred_error_steps = {p.get("step_id") for p in pred if p.get("label") != "correct"}
    hit = len(gold_error_steps.intersection(pred_error_steps))
    return hit / len(gold_error_steps)


def _taxonomy_category_hit(pred: list[dict[str, Any]], gold_codes: list[str], subject_id: str = "calculus") -> float:
    if not gold_codes:
        return 1.0
    gold_categories = {
        entry.category
        for code in gold_codes
        if (entry := lookup_error(code, subject_id=subject_id)) is not None
    }
    if not gold_categories:
        return 0.0
    pred_categories = {
        entry.category
        for item in pred
        if (entry := lookup_error(item.get("error_code", ""), subject_id=subject_id)) is not None
    }
    hit = len(gold_categories.intersection(pred_categories))
    return hit / len(gold_categories)


def _normalize_items(items: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items:
        if hasattr(item, "model_dump"):
            out.append(item.model_dump())
        elif isinstance(item, dict):
            out.append(item)
    return out


_NON_CORRECT_LABELS = {"incorrect_math", "inconsistent_or_unsupported", "unclear"}


def _error_detection_rate(pred: list[dict[str, Any]], gold: list[dict[str, Any]]) -> float:
    """Problem-level: did pred flag ANY step as non-correct when gold has errors?

    Segmentation-invariant — does not depend on step_id matching.
    Returns 1.0 if error detected, 0.0 if missed, -1.0 if gold has no errors (N/A).
    """
    gold_has_error = any(g.get("label") not in (None, "correct") for g in gold)
    if not gold_has_error:
        return -1.0  # N/A for correct cases
    pred_has_error = any(p.get("label") in _NON_CORRECT_LABELS for p in pred)
    return 1.0 if pred_has_error else 0.0


def _correct_confirmation_rate(pred: list[dict[str, Any]], gold: list[dict[str, Any]]) -> float:
    """Problem-level: did pred flag NO steps as non-correct when gold is all correct?

    Segmentation-invariant — measures false positive at problem level.
    Returns 1.0 if correctly confirmed, 0.0 if false alarm, -1.0 if gold has errors (N/A).
    """
    gold_has_error = any(g.get("label") not in (None, "correct") for g in gold)
    if gold_has_error:
        return -1.0  # N/A for error cases
    pred_has_error = any(p.get("label") in _NON_CORRECT_LABELS for p in pred)
    return 0.0 if pred_has_error else 1.0


def _error_step_accuracy(pred: list[dict[str, Any]], gold: list[dict[str, Any]]) -> float:
    """Step-level: of gold error steps, how many did pred also flag as non-correct?

    Isolates 'can the system find real errors?' from
    'does the system over-flag correct steps?'.
    Returns 1.0 if no gold error steps (N/A).
    """
    gold_errors = [g for g in gold if g.get("label") != "correct"]
    if not gold_errors:
        return 1.0
    pred_map = {p["step_id"]: p["label"] for p in pred}
    total = 0
    hit = 0
    for g in gold_errors:
        sid = g["step_id"]
        if sid in pred_map:
            total += 1
            if pred_map[sid] != "correct":
                hit += 1
    return hit / total if total else 0.0


def _correct_step_accuracy(pred: list[dict[str, Any]], gold: list[dict[str, Any]]) -> float:
    """Step-level: of gold-correct steps, how many did pred also confirm as correct?

    Measures false-alarm rate at step level.
    Returns 1.0 if no gold correct steps (N/A).
    """
    gold_corrects = [g for g in gold if g.get("label") == "correct"]
    if not gold_corrects:
        return 1.0
    pred_map = {p["step_id"]: p["label"] for p in pred}
    total = 0
    hit = 0
    for g in gold_corrects:
        sid = g["step_id"]
        if sid in pred_map:
            total += 1
            if pred_map[sid] == "correct":
                hit += 1
    return hit / total if total else 0.0


def _lenient_verification_accuracy(pred: list[dict[str, Any]], gold: list[dict[str, Any]]) -> float:
    """Step-level: same as verification_accuracy but 'unclear' gets 0.5 partial credit.

    Rewards uncertainty awareness — 'unclear' is not a wrong answer, it's an honest
    admission of uncertainty.
    """
    if not gold:
        return 1.0
    pred_map = {p["step_id"]: p["label"] for p in pred}
    total = 0
    score = 0.0
    for g in gold:
        sid = g["step_id"]
        if sid not in pred_map:
            continue
        total += 1
        gold_label = g["label"]
        pred_label = pred_map[sid]
        if pred_label == gold_label:
            score += 1.0
        elif pred_label == "unclear":
            score += 0.5
    return score / total if total else 0.0


def evaluate_cases(provider: Any, cases_file: Path, mode: str = "workflow_r1") -> dict[str, Any]:
    payload = json.loads(cases_file.read_text(encoding="utf-8"))
    cases = payload.get("cases", [])

    rows: list[dict[str, Any]] = []
    failure_reason_counter: Counter[str] = Counter()
    provider_error_type_counter: Counter[str] = Counter()
    for case in cases:
        problem = ProblemInput(**case["problem_input"])
        raw_solution = case["raw_student_solution"]
        case_subject_id = case.get("subject_id") or "calculus"
        if mode == "workflow_r1":
            out = run_tutor_graph(provider, problem, raw_solution, subject_id=case_subject_id)
        elif mode.startswith("baseline"):
            out = run_single_prompt_baseline(provider, problem, raw_solution, mode_name=mode, subject_id=case_subject_id)
        else:
            raise ValueError(f"Unsupported evaluation mode: {mode}")

        pred_verify = _normalize_items(out.get("verification_results", []))
        pred_diag = _normalize_items(out.get("diagnosis_results", []))
        pred_feedback = out.get("final_feedback")
        if hasattr(pred_feedback, "model_dump"):
            feedback_dict = pred_feedback.model_dump()
        elif isinstance(pred_feedback, dict):
            feedback_dict = pred_feedback
        else:
            feedback_dict = {}
        pred_reviews = _normalize_items(out.get("review_problems", []))
        run_meta = out.get("run_meta", {})

        verify_acc = _verification_accuracy(pred_verify, case.get("gold_verification", []))
        diag_hit = _diagnosis_hit(pred_diag, case.get("gold_diagnosis_codes", []))
        error_step_recall = _error_step_recall(pred_verify, case.get("gold_verification", []))
        taxonomy_category_hit = _taxonomy_category_hit(pred_diag, case.get("gold_diagnosis_codes", []), subject_id=case_subject_id)
        pred_first_error = _first_error_step(pred_verify)
        expected_first_error = case.get("gold_first_error_step")
        first_error_hit = 1.0 if expected_first_error == pred_first_error else 0.0
        feedback_proxy = _feedback_proxy(feedback_dict, expected_first_error)
        review_proxy = _review_relevance_proxy(pred_reviews, case.get("gold_diagnosis_codes", []))
        uncertainty_flags = out.get("uncertainty_flags", [])
        low_conf_triggered = 1.0 if "too_many_low_confidence_steps" in set(uncertainty_flags) else 0.0
        fail_reason = out.get("fail_reason")

        provider_name = str(run_meta.get("provider", ""))
        provider_events = run_meta.get("provider_events", [])
        provider_error_types = sorted(
            {
                str(e.get("error_type"))
                for e in provider_events
                if isinstance(e, dict) and e.get("error_type")
            }
        )
        real_provider_failed = 0.0
        if provider_name and provider_name != "mock":
            has_provider_error = bool(provider_error_types)
            real_provider_failed = 1.0 if (bool(out.get("fail_reason")) or has_provider_error) else 0.0
            if real_provider_failed > 0:
                failure_reason_counter[str(fail_reason or "provider_error_without_fail_reason")] += 1
                for err in provider_error_types:
                    provider_error_type_counter[err] += 1

        error_detection = _error_detection_rate(pred_verify, case.get("gold_verification", []))
        correct_confirm = _correct_confirmation_rate(pred_verify, case.get("gold_verification", []))

        rows.append(
            {
                "case_id": case.get("case_id", "unknown"),
                "verification_accuracy": verify_acc,
                "diagnosis_hit": diag_hit,
                "error_step_recall": error_step_recall,
                "taxonomy_category_hit": taxonomy_category_hit,
                "first_error_hit": first_error_hit,
                "feedback_proxy": feedback_proxy,
                "review_relevance_proxy": review_proxy,
                "error_detection_rate": error_detection,
                "correct_confirmation_rate": correct_confirm,
                "low_conf_triggered": low_conf_triggered,
                "real_provider_failed": real_provider_failed,
                "mode": mode,
                "fail_reason": fail_reason,
                "provider_error_types": provider_error_types,
                "uncertainty_flags": uncertainty_flags,
            }
        )

    n = len(rows)
    avg_verify = sum(r["verification_accuracy"] for r in rows) / n if n else 0.0
    avg_diag = sum(r["diagnosis_hit"] for r in rows) / n if n else 0.0
    avg_error_step_recall = sum(r["error_step_recall"] for r in rows) / n if n else 0.0
    avg_taxonomy_category_hit = sum(r["taxonomy_category_hit"] for r in rows) / n if n else 0.0
    avg_first_error_hit = sum(r["first_error_hit"] for r in rows) / n if n else 0.0
    avg_feedback_proxy = sum(r["feedback_proxy"] for r in rows) / n if n else 0.0
    avg_review_proxy = sum(r["review_relevance_proxy"] for r in rows) / n if n else 0.0
    avg_low_conf_trigger_rate = sum(r["low_conf_triggered"] for r in rows) / n if n else 0.0
    avg_real_provider_failure_rate = sum(r["real_provider_failed"] for r in rows) / n if n else 0.0
    avg_uncertainty_flags = sum(len(r["uncertainty_flags"]) for r in rows) / n if n else 0.0

    error_cases = [r for r in rows if r["error_detection_rate"] >= 0]
    correct_cases = [r for r in rows if r["correct_confirmation_rate"] >= 0]
    avg_error_detection = sum(r["error_detection_rate"] for r in error_cases) / len(error_cases) if error_cases else 0.0
    avg_correct_confirm = sum(r["correct_confirmation_rate"] for r in correct_cases) / len(correct_cases) if correct_cases else 0.0

    return {
        "num_cases": n,
        "avg_verification_accuracy": round(avg_verify, 4),
        "avg_diagnosis_hit": round(avg_diag, 4),
        "avg_error_step_recall": round(avg_error_step_recall, 4),
        "avg_taxonomy_category_hit": round(avg_taxonomy_category_hit, 4),
        "avg_first_error_hit": round(avg_first_error_hit, 4),
        "avg_feedback_proxy": round(avg_feedback_proxy, 4),
        "avg_review_relevance_proxy": round(avg_review_proxy, 4),
        "avg_error_detection_rate": round(avg_error_detection, 4),
        "avg_correct_confirmation_rate": round(avg_correct_confirm, 4),
        "avg_low_conf_trigger_rate": round(avg_low_conf_trigger_rate, 4),
        "avg_real_provider_failure_rate": round(avg_real_provider_failure_rate, 4),
        "avg_uncertainty_flags": round(avg_uncertainty_flags, 4),
        "real_failure_reasons": dict(failure_reason_counter),
        "real_provider_error_types": dict(provider_error_type_counter),
        "rows": rows,
    }
