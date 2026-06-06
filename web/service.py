from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Literal
from uuid import uuid4

logger = logging.getLogger(__name__)

import requests

from stem_tutor.evaluation.baseline import run_single_prompt_baseline
from stem_tutor.domain.models import ProblemInput
from stem_tutor.graph.workflow import run_tutor_graph
from stem_tutor.providers.factory import create_provider
from stem_tutor.settings import load_provider_settings
from stem_tutor.subjects.context import get_subject_context
from stem_tutor.subjects.detector import VALID_SUBJECTS, detect_subject
from stem_tutor.taxonomy.errors import lookup_error
from web import database

# Canonical subject_id defaults. Use:
#   DEFAULT_PROCESSING_SUBJECT  — for LLM calls, taxonomy lookups, prompt building
#   DEFAULT_DISPLAY_SUBJECT     — for list/report/grouping views where "no data" is fine
DEFAULT_PROCESSING_SUBJECT = "calculus"
DEFAULT_DISPLAY_SUBJECT = ""

BEIJING_TZ = timezone(timedelta(hours=8))

RUN_ATTEMPTS_BY_DEPTH = {
    "no_ref": 1,
    "with_ref": 2,
}

RECOVERABLE_UNCERTAINTY_FLAGS = {
    "too_many_low_confidence_steps",
    "manual_review_required",
    "verify_schema_fallback",
    "verify_schema_validation",
    "reference_schema_fallback",
    "reference_solution_failed",
    "unverified_steps_skipped",
    "verification_skipped_budget",
}

GENERIC_UNAVAILABLE_MESSAGE = "分析结果暂时不可用，请稍后重试。"
GENERIC_REVIEW_MESSAGE = "结果置信度不足，已进入保护模式，建议人工复核。"


def _resolve_max_run_attempts(depth: str) -> int:
    env = os.environ.get("STEM_TUTOR_MAX_RUN_ATTEMPTS", "").strip()
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            pass
    return RUN_ATTEMPTS_BY_DEPTH.get(depth, RUN_ATTEMPTS_BY_DEPTH["with_ref"])


def _is_retryable_exception(exc: Exception) -> bool:
    if isinstance(exc, requests.Timeout):
        return True
    if isinstance(exc, requests.ConnectionError):
        return True
    if isinstance(exc, requests.HTTPError):
        response = getattr(exc, "response", None)
        if response is None:
            return True
        code = int(response.status_code)
        return code == 429 or code >= 500
    msg = str(exc).lower()
    return any(token in msg for token in ("timeout", "tempor", "rate limit", "connection"))


def _derive_status_and_user_state(fail_reason: str | None, flags: list[str]) -> tuple[str, str, str]:
    flag_set = set(flags or [])
    if "manual_review_required" in flag_set:
        return "manual_review_required", "needs_review", GENERIC_REVIEW_MESSAGE
    if fail_reason:
        return "failed", "unavailable", GENERIC_UNAVAILABLE_MESSAGE
    return "success", "complete", ""


def _should_retry_response(response: dict, attempt: int, max_attempts: int) -> bool:
    if attempt >= max_attempts:
        return False

    status = str(response.get("status") or "").strip()
    user_status = str(response.get("user_status") or "").strip()
    flags = set(response.get("uncertainty_flags") or [])
    reason = str(response.get("fail_reason") or "").lower()

    if status == "success" and user_status == "complete":
        return False

    if status == "manual_review_required" or user_status == "needs_review":
        return True

    if flags.intersection(RECOVERABLE_UNCERTAINTY_FLAGS):
        return True

    retry_keywords = ("timeout", "tempor", "rate limit", "schema", "uncertainty")
    return any(k in reason for k in retry_keywords)


def _build_unavailable_response(
    problem_text: str,
    model_name: str,
    ocr_model_name: str | None,
    subject_id: str,
    depth: str,
    run_id: str | None = None,
) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    resolved_run_id = run_id or str(uuid4())
    return {
        "first_critical_step_id": None,
        "concise_summary": "",
        "likely_cause": None,
        "next_action": "",
        "caution_note": None,
        "review_concepts": [],
        "steps": [],
        "diagnoses": [],
        "review_problems": [],
        "status": "failed",
        "user_status": "unavailable",
        "user_message": GENERIC_UNAVAILABLE_MESSAGE,
        "fail_reason": "internal_unavailable",
        "uncertainty_flags": ["result_unavailable"],
        "run_meta": {
            "run_id": resolved_run_id,
            "started_at": now,
            "completed_at": now,
            "provider": "openai-compatible",
            "model": model_name,
            "ocr_model": ocr_model_name or "qwen/qwen3.6-plus",
            "subject_id": subject_id,
            "depth": depth,
            "failed": True,
            "workflow_version": "v1",
            "node_stats": {},
            "provider_events": [],
        },
        "raw_output": {},
        "tool_calls_log": [],
    }


def _retry_sleep_seconds(attempt: int) -> float:
    return min(0.8 * (2 ** max(0, attempt - 1)), 3.0)


async def _save_run_payload(run_id: str, user_id: int, payload: dict) -> None:
    meta = payload.setdefault("run_meta", {})
    if not meta.get("run_id"):
        meta["run_id"] = run_id
    if not meta.get("completed_at"):
        meta["completed_at"] = datetime.now(timezone.utc).isoformat()
    meta["failed"] = payload.get("status") == "failed"
    run_status = payload.get("status", "running")
    subject = meta.get("subject_id", DEFAULT_DISPLAY_SUBJECT)
    problem_text = ""
    raw = payload.get("raw_output", {})
    pi = raw.get("problem_input")
    if isinstance(pi, dict):
        problem_text = pi.get("problem_text", "")
    await database.save_run(run_id, user_id, payload, status=run_status, subject=subject, problem_text=problem_text)


async def _save_running_placeholder(run_id: str, user_id: int, run_meta: dict) -> None:
    row = await database.load_run(run_id, user_id)
    if row:
        return
    payload = {
        "first_critical_step_id": None,
        "concise_summary": "",
        "likely_cause": None,
        "next_action": "",
        "caution_note": None,
        "review_concepts": [],
        "steps": [],
        "diagnoses": [],
        "review_problems": [],
        "status": "running",
        "user_status": "",
        "user_message": "",
        "fail_reason": None,
        "uncertainty_flags": [],
        "run_meta": {**run_meta, "failed": False},
        "raw_output": {},
        "tool_calls_log": [],
    }
    subject = run_meta.get("subject_id", DEFAULT_DISPLAY_SUBJECT)
    await database.save_run(run_id, user_id, payload, status="running", subject=subject, problem_text="")


async def _save_intermediate_state(run_id: str, user_id: int, accumulated_state: dict) -> None:
    response = _shape_response(accumulated_state)
    response["status"] = "running"
    response["user_status"] = ""
    meta = response.get("run_meta", {})
    meta["failed"] = False
    last_node = accumulated_state.get("_last_completed_node", "")

    completed_nodes = []
    existing = await database.load_run(run_id, user_id)
    if existing:
        existing_meta = existing["data"].get("run_meta", {})
        completed_nodes = existing_meta.get("completed_nodes", [])

    if last_node and last_node not in completed_nodes:
        completed_nodes.append(last_node)
    meta["completed_nodes"] = completed_nodes
    meta["last_node"] = last_node
    meta["last_node_label"] = NODE_LABELS.get(last_node, last_node)
    response["run_meta"] = meta
    await database.update_run(run_id, response)


async def _save_run_state(run_id: str, user_id: int, state: dict):
    result = _shape_response(state)
    meta = result.get("run_meta", {})
    meta["completed_at"] = datetime.now(timezone.utc).isoformat()
    meta["failed"] = result.get("status") == "failed"
    result["run_meta"] = meta
    await database.update_run(run_id, result, status=result.get("status"))
    try:
        await _auto_record_mastery(user_id, state)
    except Exception:
        logging.getLogger(__name__).warning("auto_record_mastery failed for user %s", user_id, exc_info=True)


async def _auto_record_mastery(user_id: int, state: dict):
    diagnoses = state.get("diagnosis_results", [])
    if not diagnoses:
        return
    mastery = await database.get_mastery(user_id)
    errors = mastery.get("errors", {})
    now_iso = datetime.now(timezone.utc).isoformat()
    subject_id = (state.get("run_meta") or {}).get("subject_id", "calculus")
    diagnosed_codes = set(_get_attr(d, "error_code", "") for d in diagnoses if _get_attr(d, "error_code", ""))
    for d in diagnoses:
        error_code = _get_attr(d, "error_code", "")
        if not error_code:
            continue
        if error_code not in errors:
            errors[error_code] = {
                "total": 0, "mastered": False, "timestamps": [],
                "auto_mastered": False, "last_seen": None,
                "subject_ids": [], "consecutive_correct": {},
            }
        entry = errors[error_code]
        entry["total"] = entry.get("total", 0) + 1
        ts_list = entry.get("timestamps", [])
        ts_list.append(now_iso)
        if len(ts_list) > 50:
            ts_list[:] = ts_list[-50:]
        entry["timestamps"] = ts_list
        entry["last_seen"] = now_iso
        sids = entry.get("subject_ids", [])
        if subject_id not in sids:
            sids.append(subject_id)
        entry["subject_ids"] = sids
        cc = entry.get("consecutive_correct", {})
        if isinstance(cc, int):
            cc = {}
        cc[subject_id] = 0
        entry["consecutive_correct"] = cc
    for code in list(errors.keys()):
        if code in diagnosed_codes:
            continue
        cc = errors[code].get("consecutive_correct", {})
        if isinstance(cc, int):
            cc = {}
        cc[subject_id] = cc.get(subject_id, 0) + 1
        errors[code]["consecutive_correct"] = cc
    mastery["errors"] = errors
    history = mastery.get("analysis_history", [])
    error_codes = [_get_attr(d, "error_code", "") for d in diagnoses if _get_attr(d, "error_code", "")]
    steps = state.get("normalized_steps", [])
    verifications = state.get("verification_results", [])
    correct_count = sum(1 for v in verifications if _get_attr(v, "label", "") == "correct")
    history.append({
        "run_id": (state.get("run_meta") or {}).get("run_id", ""),
        "date": now_iso,
        "error_codes": error_codes,
        "subject_id": subject_id,
        "step_count": len(steps),
        "correct_count": correct_count,
    })
    if len(history) > 200:
        history[:] = history[-200:]
    mastery["analysis_history"] = history
    await database.save_mastery(user_id, mastery)


async def _save_run_error(run_id: str, user_id: int, error_msg: str, initial_state: dict):
    result = _shape_response(initial_state)
    result["status"] = "failed"
    result["fail_reason"] = error_msg
    meta = result.get("run_meta", {})
    meta["completed_at"] = datetime.now(timezone.utc).isoformat()
    meta["failed"] = True
    result["run_meta"] = meta
    await database.update_run(run_id, result, status="failed")


async def _load_run_result(run_id: str, user_id: int) -> dict | None:
    row = await database.load_run(run_id, user_id)
    if not row:
        return None
    return row["data"]


async def _get_run_status(run_id: str, user_id: int) -> dict:
    row = await database.load_run(run_id, user_id)
    if not row:
        return {"status": "not_found", "run_id": run_id}
    data = row["data"]
    meta = data.get("run_meta", {})
    user_status = data.get("user_status")
    if user_status == "complete":
        return {"status": "complete", "user_status": "complete", "run_id": run_id}
    if user_status == "needs_review":
        return {
            "status": "needs_review",
            "user_status": "needs_review",
            "run_id": run_id,
            "message": data.get("user_message") or GENERIC_REVIEW_MESSAGE,
        }
    if user_status == "unavailable":
        return {
            "status": "unavailable",
            "user_status": "unavailable",
            "run_id": run_id,
            "message": data.get("user_message") or GENERIC_UNAVAILABLE_MESSAGE,
        }

    status = data.get("status")
    if status == "running":
        result = {"status": "running", "run_id": run_id}
        meta = data.get("run_meta", {})
        if meta.get("last_node"):
            result["last_node"] = meta["last_node"]
            result["last_node_label"] = meta.get("last_node_label", meta["last_node"])
            result["completed_nodes"] = meta.get("completed_nodes", [])
        if data.get("steps") and len(data["steps"]) > 0:
            result["steps"] = data["steps"]
            result["steps_done"] = len(data["steps"])
        if data.get("reference_solution"):
            result["reference_solution"] = data["reference_solution"]
        if data.get("diagnoses") and len(data["diagnoses"]) > 0:
            result["diagnoses"] = data["diagnoses"]
            result["diagnoses_done"] = len(data["diagnoses"])
        if data.get("feedback") or data.get("next_action") or data.get("caution_note"):
            result["feedback"] = {
                "next_action": data.get("next_action"),
                "caution_note": data.get("caution_note"),
                "review_concepts": data.get("review_concepts", []),
                "concise_summary": data.get("concise_summary"),
                "likely_cause": data.get("likely_cause"),
            }
        if data.get("review_problems") and len(data["review_problems"]) > 0:
            result["review_problems"] = data["review_problems"]
        return result
    if status == "cancelled":
        return {"status": "cancelled", "user_status": "cancelled", "run_id": run_id}
    if status == "manual_review_required":
        return {
            "status": "needs_review",
            "user_status": "needs_review",
            "run_id": run_id,
            "message": data.get("user_message") or GENERIC_REVIEW_MESSAGE,
        }
    if status == "failed" or meta.get("failed"):
        return {
            "status": "unavailable",
            "user_status": "unavailable",
            "run_id": run_id,
            "message": data.get("user_message") or GENERIC_UNAVAILABLE_MESSAGE,
        }

    if data.get("verification_results") or data.get("steps"):
        return {"status": "complete", "user_status": "complete", "run_id": run_id}
    return {"status": "running", "run_id": run_id}


def _serialize_output(output: dict) -> dict:
    def _dump(v):
        if hasattr(v, "model_dump"):
            return v.model_dump()
        if isinstance(v, list):
            return [_dump(i) for i in v]
        return v

    return {k: _dump(v) for k, v in output.items()}


def _get_attr(item, key, default=None):
    if hasattr(item, key):
        val = getattr(item, key)
        if hasattr(val, "value"):
            return val.value
        return val
    if isinstance(item, dict):
        return item.get(key, default)
    return default


def _shape_response(state: dict) -> dict:
    feedback = state.get("final_feedback")
    steps_raw = state.get("normalized_steps", [])
    verifications = state.get("verification_results", [])
    diagnoses = state.get("diagnosis_results", [])
    reviews = state.get("review_problems", [])
    ocr_meta = state.get("ocr_meta")
    fail_reason = state.get("fail_reason")
    flags = state.get("uncertainty_flags", [])
    run_meta = state.get("run_meta", {})

    vmap = {_get_attr(v, "step_id"): v for v in verifications}
    steps = []
    for s in steps_raw:
        step_id = _get_attr(s, "step_id")
        v = vmap.get(step_id)
        if v is None:
            steps.append({
                "step_id": step_id,
                "raw_text": _get_attr(s, "raw_text", ""),
                "label": "unverified",
                "evidence": "此步骤未被验证（可能是验证流程异常）。",
                "confidence": 0.0,
                "violated_principles": ["verification_missing"],
                "sympy_verified": False,
                "sympy_equivalent": None,
            })
        else:
            steps.append({
                "step_id": step_id,
                "raw_text": _get_attr(s, "raw_text", ""),
                "label": _get_attr(v, "label", "unclear"),
                "evidence": _get_attr(v, "evidence", ""),
                "confidence": _get_attr(v, "confidence", 0.0),
                "violated_principles": _get_attr(v, "violated_principles", []),
                "sympy_verified": _get_attr(v, "sympy_verified", False),
                "sympy_equivalent": _get_attr(v, "sympy_equivalent", None),
            })

    shaped_diagnoses = []
    for d in diagnoses:
        error_code = _get_attr(d, "error_code", "")
        subject_id = run_meta.get("subject_id") or DEFAULT_PROCESSING_SUBJECT
        entry = lookup_error(error_code, subject_id=subject_id)
        shaped_diagnoses.append({
            "step_id": _get_attr(d, "step_id", ""),
            "error_code": error_code,
            "category": _get_attr(d, "category", ""),
            "short_desc": entry.short_desc if entry else "",
            "root_cause_hypothesis": _get_attr(d, "root_cause_hypothesis", ""),
            "supporting_evidence": _get_attr(d, "supporting_evidence", ""),
            "confidence": _get_attr(d, "confidence", 0.0),
        })

    status, user_status, user_message = _derive_status_and_user_state(fail_reason, flags)

    run_meta_serialized = _serialize_output(run_meta) if run_meta else {}
    run_meta_serialized["failed"] = status == "failed"
    raw_output_serialized = _serialize_output(state)
    if isinstance(raw_output_serialized, dict):
        raw_meta = raw_output_serialized.get("run_meta")
        if isinstance(raw_meta, dict):
            raw_meta["failed"] = status == "failed"
        raw_output_serialized["global_budget"] = state.get("global_budget")

    problem_input = state.get("problem_input")
    ocr_text = None
    if problem_input and getattr(problem_input, "source_type", None) == "ocr":
        ocr_text = state.get("raw_student_solution", "")

    if hasattr(feedback, "model_dump"):
        feedback = feedback.model_dump()

    review_list = []
    for p in reviews:
        if hasattr(p, "model_dump"):
            review_list.append(p.model_dump())
        elif isinstance(p, dict):
            review_list.append(p)

    ref_sol = state.get("reference_solution")
    if ref_sol is not None and hasattr(ref_sol, "model_dump"):
        ref_sol = ref_sol.model_dump()

    response = {
        "first_critical_step_id": _get_attr(feedback, "first_critical_step_id") if feedback else None,
        "concise_summary": _get_attr(feedback, "concise_summary", "") if feedback else "",
        "likely_cause": _get_attr(feedback, "likely_cause") if feedback else None,
        "next_action": _get_attr(feedback, "next_action", "") if feedback else "",
        "caution_note": _get_attr(feedback, "caution_note") if feedback else None,
        "review_concepts": _get_attr(feedback, "review_concepts", []) if feedback else [],
        "steps": steps,
        "diagnoses": shaped_diagnoses,
        "review_problems": review_list,
        "status": status,
        "user_status": user_status,
        "user_message": user_message,
        "fail_reason": fail_reason,
        "uncertainty_flags": flags,
        "run_meta": run_meta_serialized,
        "raw_output": raw_output_serialized,
        "tool_calls_log": state.get("tool_calls_log", []),
        "reference_solution": ref_sol,
    }

    if ocr_meta:
        response["ocr_meta"] = ocr_meta
    if ocr_text is not None:
        response["ocr_extracted_text"] = ocr_text

    return response


def _build_partial(node_name: str, node_output: dict, accumulated_state: dict) -> dict | None:
    if node_name == "parse_student_solution":
        steps_raw = node_output.get("normalized_steps", [])
        if not steps_raw:
            return None
        return {
            "steps": [
                {"step_id": s.step_id, "raw_text": s.raw_text, "label": "pending", "evidence": "", "confidence": 0.0, "violated_principles": []}
                for s in steps_raw
            ],
        }

    if node_name == "verify_steps":
        steps_raw = accumulated_state.get("normalized_steps", [])
        verifications = accumulated_state.get("verification_results", [])
        vmap = {v.step_id: v for v in verifications}
        steps = []
        for s in steps_raw:
            v = vmap.get(s.step_id)
            if v is None:
                steps.append({
                    "step_id": s.step_id,
                    "raw_text": s.raw_text,
                    "label": "unverified",
                    "evidence": "此步骤未被验证。",
                    "confidence": 0.0,
                    "violated_principles": ["verification_missing"],
                })
            else:
                steps.append({
                    "step_id": s.step_id,
                    "raw_text": s.raw_text,
                    "label": v.label.value if v else "unclear",
                    "evidence": v.evidence if v else "",
                    "confidence": v.confidence if v else 0.0,
                    "violated_principles": v.violated_principles if v else [],
                })
        return {"steps": steps}

    if node_name == "diagnose_error":
        diagnoses = accumulated_state.get("diagnosis_results", [])
        if not diagnoses:
            return None
        shaped = []
        subject_id = (accumulated_state.get("run_meta") or {}).get("subject_id") or DEFAULT_PROCESSING_SUBJECT
        for d in diagnoses:
            entry = lookup_error(d.error_code, subject_id=subject_id)
            shaped.append({
                "step_id": d.step_id,
                "error_code": d.error_code,
                "category": d.category,
                "short_desc": entry.short_desc if entry else "",
                "root_cause_hypothesis": d.root_cause_hypothesis,
                "supporting_evidence": d.supporting_evidence,
                "confidence": d.confidence,
            })
        return {"diagnoses": shaped}

    if node_name == "generate_feedback":
        feedback = accumulated_state.get("final_feedback")
        if not feedback:
            return None
        return {
            "first_critical_step_id": feedback.first_critical_step_id,
            "concise_summary": feedback.concise_summary,
            "likely_cause": feedback.likely_cause,
            "next_action": feedback.next_action,
            "caution_note": feedback.caution_note,
            "review_concepts": feedback.review_concepts or [],
        }

    if node_name == "generate_review_problems":
        reviews = accumulated_state.get("review_problems", [])
        if not reviews:
            return None
        return {"review_problems": [p.model_dump() for p in reviews]}

    return None


def _detect_mime(image_bytes: bytes) -> str:
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if image_bytes[:3] == b"GIF":
        return "image/gif"
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _strip_markdown_fences(text: str) -> str:
    out = text.strip()
    if out.startswith("```"):
        first_newline = out.find("\n")
        if first_newline >= 0:
            out = out[first_newline + 1:]
        else:
            out = out[3:]
    closing = out.rfind("```")
    if closing > 0:
        out = out[:closing]
    return out.strip()


def _fix_json_escapes(text: str) -> str:
    valid_escapes = set('"\\/bnrt')
    result = []
    i = 0
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            if nxt in valid_escapes:
                result.append(text[i])
                result.append(nxt)
                i += 2
            elif nxt == "u" and i + 5 < len(text) and all(c in "0123456789abcdefABCDEF" for c in text[i + 2 : i + 6]):
                result.append(text[i : i + 6])
                i += 6
            else:
                result.append("\\\\")
                result.append(nxt)
                i += 2
        else:
            result.append(text[i])
            i += 1
    return "".join(result)


def _extract_json_object(text: str) -> dict | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape_next = False
    for i in range(start, len(text)):
        c = text[i]
        if escape_next:
            escape_next = False
            continue
        if c == "\\":
            escape_next = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    fixed = _fix_json_escapes(candidate)
                    try:
                        return json.loads(fixed)
                    except json.JSONDecodeError:
                        pass
                return None
    return None


def _parse_json_text(raw: str) -> dict | None:
    text = _strip_markdown_fences(raw)

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass

    obj = _extract_json_object(text)
    if obj is not None:
        return obj

    fixed = _fix_json_escapes(text)
    try:
        parsed = json.loads(fixed)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass

    return _extract_json_object(fixed)


OCR_FALLBACK_MODELS = ["qwen/qwen3.6-plus", "deepseek/deepseek-v3.2"]
OCR_MAX_RETRIES = 3
OCR_RETRY_DELAY = 2


def _ocr_vision_request(image_bytes: bytes, settings, model: str) -> requests.Response:
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    mime = _detect_mime(image_bytes)
    data_url = f"data:{mime};base64,{b64}"
    url = f"{settings.base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.api_key}",
        "Content-Type": "application/json",
    }

    schema_hint = '{"text": "string", "quality_score": 0.0, "warnings": ["string"]}'
    text_instruction = (
        "Transcribe all text and mathematical formulas from this image. "
        "Preserve line-by-line step structure. Use LaTeX notation for math "
        "(e.g. \\\\frac{a}{b}, \\\\int_0^1, \\\\sqrt{x}). "
        "Return ONLY a valid JSON object with no extra text before or after. "
        "Do NOT wrap in markdown code fences. "
        f"Schema: {schema_hint}  "
        "All backslashes in LaTeX MUST be double-escaped (\\\\\\\\frac, \\\\\\\\int). "
        "quality_score must be a float between 0.0 and 1.0."
    )

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You are a precise JSON API for OCR transcription of math content.",
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": text_instruction},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
        "temperature": 0.2,
        "max_tokens": 2000,
    }

    return requests.post(url, json=payload, headers=headers, timeout=settings.timeout_seconds)


def _ocr_via_vision_api(image_bytes: bytes, settings) -> dict:
    import logging
    import time

    ocr_model = settings.resolve_model_name("ocr")
    candidates = [ocr_model] + [m for m in OCR_FALLBACK_MODELS if m != ocr_model]
    all_errors: list[str] = []

    for model in candidates:
        for attempt in range(OCR_MAX_RETRIES):
            try:
                resp = _ocr_vision_request(image_bytes, settings, model)
                if not resp.ok:
                    err_detail = resp.text[:300]
                    logging.warning("[OCR] model=%s HTTP %s: %s", model, resp.status_code, err_detail)
                    all_errors.append(f"HTTP {resp.status_code} model={model} body={err_detail}")
                    if attempt < OCR_MAX_RETRIES - 1:
                        time.sleep(OCR_RETRY_DELAY)
                    continue

                content = resp.json()["choices"][0]["message"]["content"]
                parsed = _parse_json_text(content)
                if parsed is not None:
                    text = str(parsed.get("text", "")).strip()
                    if text:
                        qs = float(parsed.get("quality_score", 0.7))
                        if qs > 1.0:
                            qs = qs / 100.0
                        qs = max(0.0, min(1.0, qs))
                        result_warnings = list(parsed.get("warnings", []))
                        if model != ocr_model:
                            result_warnings.insert(0, f"ocr_model={model} (attempt {attempt + 1})")
                        return {
                            "text": text,
                            "quality_score": qs,
                            "warnings": result_warnings,
                        }

                raw_text = content.strip()
                if raw_text:
                    cleaned = _strip_markdown_fences(raw_text)
                    cleaned = re.sub(r'^\{[\s\S]*?"text"\s*:\s*"', '', cleaned, count=1)
                    if cleaned != raw_text:
                        end_match = re.search(r'"(?:\s*,[\s\S]*)?}', cleaned)
                        if end_match:
                            cleaned = cleaned[:end_match.start()]
                    cleaned = cleaned.strip().strip('"').strip()
                    if not cleaned:
                        cleaned = raw_text
                    return {
                        "text": cleaned,
                        "quality_score": 0.5,
                        "warnings": ["json_parse_failed_used_raw_text"],
                    }

                all_errors.append(f"model={model} attempt {attempt + 1} returned empty content")
            except Exception as exc:
                logging.warning("[OCR] model=%s attempt %s exception: %s", model, attempt + 1, exc)
                all_errors.append(f"model={model} attempt {attempt + 1} exception={exc}")
                if attempt < OCR_MAX_RETRIES - 1:
                    time.sleep(1)

    return {"text": "", "quality_score": 0.0, "warnings": all_errors or ["all_ocr_models_failed"]}


def ocr_problem_text(image_bytes: bytes, provider_name: str = "mock") -> dict:
    if provider_name == "mock":
        settings = load_provider_settings()
        ocr_provider = create_provider(provider_name, settings, model_group="ocr")
        payload = base64.b64encode(image_bytes).decode("utf-8")
        out = ocr_provider.ocr_to_text(payload)
        return {
            "text": str(out.get("text", "")).strip(),
            "quality_score": float(out.get("quality_score", 0.5)),
            "warnings": list(out.get("warnings", [])),
        }

    settings = load_provider_settings()
    try:
        return _ocr_via_vision_api(image_bytes, settings)
    except Exception as exc:
        return {
            "text": "",
            "quality_score": 0.0,
            "warnings": [f"vision_api_error: {exc}"],
        }


def run_stem_tutor(
    problem_text: str,
    raw_student_solution: str = "",
    source_type: Literal["text", "ocr"] = "text",
    image_bytes: bytes | None = None,
    provider_name: str = "openai-compatible",
    model_name: str = "qwen/qwen3.6-plus",
    ocr_model_name: str | None = "qwen/qwen3.6-plus",
    subject_id: str = "calculus",
    mode: str = "workflow_r1",
    depth: str = "with_ref",
    user_id: int | None = None,
) -> dict:
    ocr_payload = None
    settings = load_provider_settings()
    settings.__dict__["reasoning_model_name"] = model_name

    budget_enabled = depth == "no_ref"

    if subject_id == "auto_detect":
        subject_id = detect_subject(
            problem_text,
            base_url=settings.base_url,
            api_key=settings.api_key,
            model=settings.detection_model_name,
        )
    if not subject_id or subject_id not in VALID_SUBJECTS:
        subject_id = "calculus"

    if subject_id:
        settings.__dict__["subject_id"] = subject_id
        from stem_tutor.prompts.templates import active_subject_scope as _subj_scope
        _subj_scope_cm = _subj_scope(subject_id)
        _subj_scope_cm.__enter__()

    problem_input = ProblemInput(
        problem_id=f"web-{uuid4().hex[:8]}",
        problem_text=problem_text,
        source_type=source_type,
        ocr_payload=ocr_payload,
    )

    max_attempts = _resolve_max_run_attempts(depth)
    response: dict | None = None
    last_exc: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            if mode.startswith("baseline"):
                provider = create_provider(provider_name, settings, model_group="reasoning")
                state = run_single_prompt_baseline(provider, problem_input, raw_student_solution, mode_name=mode, subject_id=subject_id)
                response = _shape_response(state)
            else:
                provider = create_provider(provider_name, settings, model_group="reasoning")
                ocr_provider = create_provider(provider_name, settings, model_group="ocr")
                fast_provider = create_provider(provider_name, settings, model_group="fast")
                verify_group = settings.verify_model_group
                verify_provider = create_provider(provider_name, settings, model_group=verify_group)
                if settings.verify_model_name:
                    verify_provider.model_name = settings.verify_model_name

                state = run_tutor_graph(
                    provider,
                    problem_input,
                    raw_student_solution,
                    ocr_provider=ocr_provider,
                    fast_provider=fast_provider,
                    verify_provider=verify_provider,
                    budget_metadata={"depth": depth},
                    budget_enabled=budget_enabled,
                    subject_id=subject_id,
                )

                response = _shape_response(state)
        except Exception as exc:
            last_exc = exc
            if attempt < max_attempts and _is_retryable_exception(exc):
                time.sleep(_retry_sleep_seconds(attempt))
                continue
            break

        if response is None:
            continue

        if _should_retry_response(response, attempt, max_attempts):
            time.sleep(_retry_sleep_seconds(attempt))
            continue

        break

    if response is None:
        response = _build_unavailable_response(
            problem_text=problem_text,
            model_name=model_name,
            ocr_model_name=ocr_model_name,
            subject_id=subject_id,
            depth=depth,
        )
        if last_exc is not None:
            meta = response.get("run_meta", {})
            meta["last_error_type"] = type(last_exc).__name__
            response["run_meta"] = meta

    meta = response.get("run_meta", {})
    meta["ocr_model"] = ocr_model_name or "qwen/qwen3.6-plus"
    meta["subject_id"] = subject_id
    meta["depth"] = depth
    meta["attempt"] = min(attempt, max_attempts)
    meta["max_attempts"] = max_attempts
    meta["failed"] = response.get("status") == "failed"
    response["run_meta"] = meta

    if subject_id:
        try:
            _subj_scope_cm.__exit__(None, None, None)
        except Exception:
            pass

    return response


NODE_LABELS = {
    "parse_student_solution": "解析解题步骤",
    "generate_reference_solution": "生成参考解答",
    "verify_steps": "验证每步正确性",
    "diagnose_error": "诊断错误原因",
    "generate_feedback": "生成学习反馈",
    "generate_review_problems": "生成复习练习",
    "finalize_report": "生成最终报告",
}


def _serialize_partial(node_name: str, state: dict) -> dict | None:
    if node_name == "parse_student_solution":
        steps = state.get("normalized_steps", [])
        return {
            "steps": [
                {"step_id": s.step_id, "raw_text": s.raw_text}
                for s in steps
            ]
        }

    if node_name == "verify_steps":
        steps_raw = state.get("normalized_steps", [])
        verifications = state.get("verification_results", [])
        vmap = {v.step_id: v for v in verifications}
        shaped_steps = []
        for s in steps_raw:
            v = vmap.get(s.step_id)
            if v is None:
                shaped_steps.append({
                    "step_id": s.step_id,
                    "raw_text": s.raw_text,
                    "label": "unverified",
                    "evidence": "此步骤未被验证。",
                    "confidence": 0.0,
                    "violated_principles": ["verification_missing"],
                    "sympy_verified": False,
                    "sympy_equivalent": None,
                })
            else:
                shaped_steps.append({
                    "step_id": s.step_id,
                    "raw_text": s.raw_text,
                    "label": v.label.value,
                    "evidence": v.evidence,
                    "confidence": v.confidence,
                    "violated_principles": v.violated_principles,
                    "sympy_verified": v.sympy_verified,
                    "sympy_equivalent": v.sympy_equivalent,
                })
        return {"steps": shaped_steps}

    if node_name == "diagnose_error":
        diagnoses = state.get("diagnosis_results", [])
        shaped = []
        subject_id = (state.get("run_meta") or {}).get("subject_id") or DEFAULT_PROCESSING_SUBJECT
        for d in diagnoses:
            entry = lookup_error(d.error_code, subject_id=subject_id)
            shaped.append({
                "step_id": d.step_id,
                "error_code": d.error_code,
                "category": d.category,
                "short_desc": entry.short_desc if entry else "",
                "root_cause_hypothesis": d.root_cause_hypothesis,
                "supporting_evidence": d.supporting_evidence,
                "confidence": d.confidence,
            })
        return {"diagnoses": shaped}

    if node_name == "generate_feedback":
        feedback = state.get("final_feedback")
        if not feedback:
            return None
        return {
            "first_critical_step_id": feedback.first_critical_step_id,
            "concise_summary": feedback.concise_summary,
            "likely_cause": feedback.likely_cause,
            "next_action": feedback.next_action,
            "caution_note": feedback.caution_note,
            "review_concepts": feedback.review_concepts or [],
        }

    if node_name == "generate_review_problems":
        reviews = state.get("review_problems", [])
        return {"review_problems": [p.model_dump() for p in reviews]}

    if node_name == "generate_reference_solution":
        ref = state.get("reference_solution")
        if ref and hasattr(ref, "model_dump"):
            return {"reference_solution": ref.model_dump()}
        return {"reference_solution": ref}

    return None


_SSE_HEARTBEAT_INTERVAL = 30
_cancel_events: dict[str, "asyncio.Event"] = {}


async def cancel_run(run_id: str, user_id: int) -> bool:
    row = await database.load_run(run_id, user_id)
    if not row:
        return False
    evt = _cancel_events.get(run_id)
    if evt is None:
        return False
    evt.set()
    return True


async def _with_heartbeat(agen, interval=_SSE_HEARTBEAT_INTERVAL, cancel_event=None):
    import asyncio
    pending_task = None
    while True:
        try:
            if cancel_event is not None and cancel_event.is_set():
                if pending_task is not None:
                    pending_task.cancel()
                return
            if pending_task is None:
                pending_task = asyncio.ensure_future(agen.__anext__())
            done, _ = await asyncio.wait({pending_task}, timeout=interval)
            if done:
                event = pending_task.result()
                pending_task = None
                yield ("event", event)
            else:
                if cancel_event is not None and cancel_event.is_set():
                    if pending_task is not None:
                        pending_task.cancel()
                    return
                yield ("heartbeat", None)
        except StopAsyncIteration:
            break


async def run_stem_tutor_stream(
    problem_text: str,
    raw_student_solution: str = "",
    source_type: Literal["text", "ocr"] = "text",
    image_bytes: bytes | None = None,
    provider_name: str = "openai-compatible",
    model_name: str = "qwen/qwen3.6-plus",
    ocr_model_name: str | None = "qwen/qwen3.6-plus",
    run_id: str | None = None,
    subject_id: str = "calculus",
    mode: str = "workflow_r1",
    depth: str = "with_ref",
    user_id: int | None = None,
):
    import asyncio
    import json as _json
    from stem_tutor.graph.workflow import build_tutor_graph

    if run_id is None:
        run_id = str(uuid4())

    cancel_event = asyncio.Event()
    _cancel_events[run_id] = cancel_event

    settings = load_provider_settings()
    settings.__dict__["reasoning_model_name"] = model_name

    budget_enabled = depth == "no_ref"

    if subject_id == "auto_detect":
        subject_id = detect_subject(
            problem_text,
            base_url=settings.base_url,
            api_key=settings.api_key,
            model=settings.detection_model_name,
        )
    if not subject_id or subject_id not in VALID_SUBJECTS:
        subject_id = "calculus"

    if subject_id:
        settings.__dict__["subject_id"] = subject_id
        get_subject_context(subject_id)  # warmup the subject context cache
        from stem_tutor.prompts.templates import set_active_subject
        set_active_subject(subject_id)

    ocr_payload = None
    if source_type == "ocr" and image_bytes:
        ocr_payload = base64.b64encode(image_bytes).decode("utf-8")

    problem_input = ProblemInput(
        problem_id=f"web-{uuid4().hex[:8]}",
        problem_text=problem_text,
        source_type=source_type,
        ocr_payload=ocr_payload,
    )

    provider_info = {"provider_name": provider_name, "model_name": model_name}

    started_at = datetime.now(timezone.utc).isoformat()
    max_attempts = _resolve_max_run_attempts(depth)

    yield f"data: {_json.dumps({'type': 'start', 'run_id': run_id, 'message': '开始分析', 'depth': depth, 'max_attempts': max_attempts}, ensure_ascii=False)}\n\n"

    await _save_running_placeholder(run_id, user_id, {
        "run_id": run_id,
        "started_at": started_at,
        "provider": provider_info.get("provider_name", "unknown"),
        "model": provider_info.get("model_name", "unknown"),
        "ocr_model": ocr_model_name or "qwen/qwen3.6-plus",
        "subject_id": subject_id,
        "mode": mode,
        "workflow_version": "v1",
        "depth": depth,
        "node_stats": {},
        "provider_events": [],
    })

    final_response: dict | None = None
    final_state: dict | None = None
    last_exc: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        initial_state = {
            "problem_input": problem_input,
            "raw_student_solution": raw_student_solution,
            "trace": [],
            "run_meta": {
                "run_id": run_id,
                "started_at": started_at,
                "provider": provider_info.get("provider_name", "unknown"),
                "model": provider_info.get("model_name", "unknown"),
                "ocr_model": ocr_model_name or "qwen/qwen3.6-plus",
                "subject_id": subject_id,
                "mode": mode,
                "workflow_version": "v1",
                "depth": depth,
                "node_stats": {},
                "provider_events": [],
                "attempt": attempt,
                "max_attempts": max_attempts,
            },
            "budget_metadata": {"depth": depth},
            "budget_enabled": budget_enabled,
            "subject_id": subject_id,
        }

        accumulated_state = dict(initial_state)

        if attempt > 1:
            yield f"data: {_json.dumps({'type': 'retrying', 'attempt': attempt, 'max_attempts': max_attempts, 'message': f'第 {attempt} 次尝试中...'}, ensure_ascii=False)}\n\n"

        try:
            if mode.startswith("baseline"):
                provider = create_provider(provider_name, settings, model_group="reasoning")
                state = run_single_prompt_baseline(provider, problem_input, raw_student_solution, mode_name=mode, subject_id=subject_id)
                state["run_meta"] = {**initial_state["run_meta"], **state.get("run_meta", {})}
                response = _shape_response(state)
                accumulated_state = dict(state)
            else:
                provider = create_provider(provider_name, settings, model_group="reasoning")
                ocr_provider = create_provider(provider_name, settings, model_group="ocr")
                fast_provider = create_provider(provider_name, settings, model_group="fast")
                verify_group = settings.verify_model_group
                verify_provider = create_provider(provider_name, settings, model_group=verify_group)
                if settings.verify_model_name:
                    verify_provider.model_name = settings.verify_model_name

                app = build_tutor_graph(provider, ocr_provider=ocr_provider, fast_provider=fast_provider, verify_provider=verify_provider)
                async for tag, event in _with_heartbeat(app.astream(initial_state, stream_mode="updates"), cancel_event=cancel_event):
                    if tag == "heartbeat":
                        yield ": keepalive\n\n"
                        continue
                    for node_name, node_output in event.items():
                        if node_name in ("__end__", "__interrupt__"):
                            continue

                        for key, value in node_output.items():
                            accumulated_state[key] = value

                        accumulated_state["_last_completed_node"] = node_name

                        label = NODE_LABELS.get(node_name, node_name)
                        yield f"data: {_json.dumps({'type': 'node_start', 'node': node_name, 'label': label}, ensure_ascii=False)}\n\n"

                        if node_name == "parse_student_solution":
                            steps = node_output.get("normalized_steps", [])
                            step_count = len(steps)
                            yield f"data: {_json.dumps({'type': 'progress', 'node': node_name, 'detail': f'解析到 {step_count} 个解题步骤'}, ensure_ascii=False)}\n\n"

                        elif node_name == "generate_reference_solution":
                            ref_sol = node_output.get("reference_solution", {})
                            if isinstance(ref_sol, dict):
                                ref_text = ref_sol.get("reference_text", "")
                            elif hasattr(ref_sol, "reference_text"):
                                ref_text = ref_sol.reference_text
                            else:
                                ref_text = ""
                            is_valid = ref_text and not ref_text.startswith("Reference solution unavailable")
                            detail = "参考解答已生成" if is_valid else "参考解答生成失败，将跳过逐句验证"
                            yield f"data: {_json.dumps({'type': 'progress', 'node': node_name, 'detail': detail}, ensure_ascii=False)}\n\n"

                        elif node_name == "verify_steps":
                            verifications = node_output.get("verification_results", [])
                            step_count = len(verifications)
                            detail = f"已验证 {step_count} 个步骤"
                            yield f"data: {_json.dumps({'type': 'progress', 'node': node_name, 'detail': detail}, ensure_ascii=False)}\n\n"

                        elif node_name == "diagnose_error":
                            diagnoses = node_output.get("diagnosis_results", [])
                            detail = f"诊断到 {len(diagnoses)} 个错误" if diagnoses else "未发现明显错误"
                            yield f"data: {_json.dumps({'type': 'progress', 'node': node_name, 'detail': detail}, ensure_ascii=False)}\n\n"

                        elif node_name == "generate_feedback":
                            yield f"data: {_json.dumps({'type': 'progress', 'node': node_name, 'detail': '学习反馈已生成'}, ensure_ascii=False)}\n\n"

                        elif node_name == "generate_review_problems":
                            reviews = node_output.get("review_problems", [])
                            detail = f"生成 {len(reviews)} 道复习题" if reviews else "未生成复习题"
                            yield f"data: {_json.dumps({'type': 'progress', 'node': node_name, 'detail': detail}, ensure_ascii=False)}\n\n"

                        partial = _serialize_partial(node_name, accumulated_state)
                        node_done_event = {"type": "node_done", "node": node_name, "label": label}
                        if partial is not None:
                            node_done_event["partial"] = partial
                        yield f"data: {_json.dumps(node_done_event, ensure_ascii=False)}\n\n"

                        try:
                            await _save_intermediate_state(run_id, user_id, accumulated_state)
                        except Exception:
                            pass

            if cancel_event is not None and cancel_event.is_set():
                yield f"data: {_json.dumps({'type': 'cancelled', 'message': '分析已被用户取消'}, ensure_ascii=False)}\n\n"
                _cancel_events.pop(run_id, None)
                cancelled_response = _shape_response(accumulated_state)
                cancelled_response["status"] = "cancelled"
                cancelled_response["user_status"] = "cancelled"
                cancelled_response["user_message"] = "分析已被用户取消"
                await _save_run_payload(run_id, user_id, cancelled_response)
                return

            response = _shape_response(accumulated_state)
        except asyncio.CancelledError:
            try:
                cancelled_response = _shape_response(accumulated_state)
                cancelled_response["status"] = "cancelled"
                cancelled_response["user_status"] = "cancelled"
                cancelled_response["user_message"] = "分析已被用户取消"
                await _save_run_payload(run_id, user_id, cancelled_response)
            except Exception:
                pass
            _cancel_events.pop(run_id, None)
            return
        except Exception as exc:
            last_exc = exc
            import logging as _logging
            _logging.getLogger(__name__).error(
                "[Analyze] Graph execution failed (attempt %d/%d): %s: %s",
                attempt, max_attempts, type(exc).__name__, exc,
                exc_info=True,
            )
            if attempt < max_attempts and _is_retryable_exception(exc):
                delay = _retry_sleep_seconds(attempt)
                yield f"data: {_json.dumps({'type': 'retrying', 'attempt': attempt + 1, 'max_attempts': max_attempts, 'message': '网络或模型暂时不稳定，正在自动重试...'}, ensure_ascii=False)}\n\n"
                await asyncio.sleep(delay)
                continue
            break

        meta = response.get("run_meta", {})
        meta["ocr_model"] = ocr_model_name or "qwen/qwen3.6-plus"
        meta["subject_id"] = subject_id
        meta["depth"] = depth
        meta["run_id"] = run_id
        meta["attempt"] = attempt
        meta["max_attempts"] = max_attempts
        meta["started_at"] = meta.get("started_at") or started_at
        meta["failed"] = response.get("status") == "failed"
        response["run_meta"] = meta
        accumulated_state["run_meta"] = meta

        final_response = response
        final_state = accumulated_state

        if _should_retry_response(response, attempt, max_attempts):
            if attempt < max_attempts:
                delay = _retry_sleep_seconds(attempt)
                yield f"data: {_json.dumps({'type': 'retrying', 'attempt': attempt + 1, 'max_attempts': max_attempts, 'message': '结果置信度不足，正在自动重试...'}, ensure_ascii=False)}\n\n"
                await asyncio.sleep(delay)
                continue
        break

    if final_response is None:
        final_response = _build_unavailable_response(
            problem_text=problem_text,
            model_name=model_name,
            ocr_model_name=ocr_model_name,
            subject_id=subject_id,
            depth=depth,
            run_id=run_id,
        )
        meta = final_response.get("run_meta", {})
        meta["attempt"] = max_attempts
        meta["max_attempts"] = max_attempts
        if last_exc is not None:
            meta["last_error_type"] = type(last_exc).__name__
        final_response["run_meta"] = meta
        await _save_run_payload(run_id, user_id, final_response)
        yield f"data: {_json.dumps({'type': 'safe_error', 'message': final_response.get('user_message') or GENERIC_UNAVAILABLE_MESSAGE}, ensure_ascii=False)}\n\n"
        yield f"data: {_json.dumps({'type': 'result', 'data': final_response}, ensure_ascii=False)}\n\n"
        yield f"data: {_json.dumps({'type': 'done', 'message': '分析完成'}, ensure_ascii=False)}\n\n"
        _cancel_events.pop(run_id, None)
        return

    if final_state is not None:
        final_state["run_meta"] = final_response.get("run_meta", {})

    await _save_run_payload(run_id, user_id, final_response)
    yield f"data: {_json.dumps({'type': 'result', 'data': final_response}, ensure_ascii=False)}\n\n"
    yield f"data: {_json.dumps({'type': 'done', 'message': '分析完成'}, ensure_ascii=False)}\n\n"
    _cancel_events.pop(run_id, None)
    return


async def list_runs(
    user_id: int,
    subject: str | None = None,
    status: str | None = None,
    search: str | None = None,
    page: int = 1,
    per_page: int = 20,
) -> dict:
    result = await database.list_runs_db(user_id, subject=subject, status=status, search=search, page=page, per_page=per_page)
    runs = []
    for row in result["runs"]:
        data = row["data"]
        meta = data.get("run_meta", {})
        run_status = data.get("user_status") or data.get("status", "unknown")
        if run_status == "success":
            run_status = "complete"
        elif run_status == "manual_review_required":
            run_status = "needs_review"
        elif run_status == "failed":
            run_status = "unavailable"

        raw = data.get("raw_output", {})
        problem_input = raw.get("problem_input", {})
        problem_text = ""
        if isinstance(problem_input, dict):
            problem_text = problem_input.get("problem_text", "")
        if not problem_text:
            problem_text = row.get("problem_text", "") or meta.get("problem_text", "")

        subject_id = meta.get("subject_id", DEFAULT_DISPLAY_SUBJECT) or row.get("subject", "")

        started = meta.get("started_at", "")
        completed = meta.get("completed_at", "")
        duration_seconds = None
        if started and completed:
            try:
                from datetime import datetime, timezone
                st = datetime.fromisoformat(started)
                ct = datetime.fromisoformat(completed)
                duration_seconds = int((ct - st).total_seconds())
            except Exception:
                pass

        display_name = subject_id
        try:
            from stem_tutor.subjects.context import get_subject_context
            ctx = get_subject_context(subject_id)
            display_name = ctx.display_name
        except Exception:
            pass

        runs.append({
            "run_id": meta.get("run_id", row.get("id", "")),
            "problem_preview": (problem_text[:80] + "...") if len(problem_text) > 80 else problem_text,
            "subject": subject_id,
            "subject_display": display_name,
            "timestamp": completed or started or row.get("created_at", ""),
            "status": run_status,
            "user_status": data.get("user_status", run_status),
            "user_message": data.get("user_message", ""),
            "duration_seconds": duration_seconds,
            "mode": meta.get("mode", ""),
            "model": meta.get("model", ""),
            "depth": meta.get("depth", ""),
        })

    return {"runs": runs, "total": result["total"], "page": result["page"], "per_page": result["per_page"]}


async def delete_runs(user_id: int, run_ids: list[str]) -> dict:
    deleted = await database.delete_runs_db(user_id, run_ids)
    return {"deleted": deleted, "not_found": []}


async def cleanup_runs_before(user_id: int, days: int) -> dict:
    from datetime import datetime, timezone, timedelta

    if days <= 0:
        cutoff = datetime.now(timezone.utc)
    else:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    deleted = await database.cleanup_runs_db(user_id, cutoff.isoformat())
    return {"deleted": deleted, "cutoff_date": cutoff.isoformat()}


async def _update_step_in_run(run_id: str, user_id: int, step_id: str, new_result) -> None:
    row = await database.load_run(run_id, user_id)
    if not row:
        return
    data = row["data"]

    for s in data.get("steps", []):
        if s.get("step_id") == step_id:
            s["label"] = new_result.label.value
            s["evidence"] = new_result.evidence
            s["confidence"] = new_result.confidence
            s["violated_principles"] = new_result.violated_principles
            s["sympy_verified"] = new_result.sympy_verified
            s["sympy_equivalent"] = new_result.sympy_equivalent

    for vr in data.get("raw_output", {}).get("verification_results", []):
        if vr.get("step_id") == step_id:
            vr.update(new_result.model_dump())

    data.setdefault("reverify_history", []).append({
        "step_id": step_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "new_result": new_result.model_dump(),
    })

    await database.update_run(run_id, data)


async def reverify_step(run_id: str, user_id: int, step_id: str) -> dict:
    run_data = await _load_run_result(run_id, user_id)
    if not run_data:
        return {"success": False, "error": "运行记录不存在"}

    raw = run_data.get("raw_output", {})
    steps_raw = raw.get("normalized_steps", [])

    target = None
    target_idx = -1
    for i, s in enumerate(steps_raw):
        if s.get("step_id") == step_id:
            target = s
            target_idx = i
            break

    if target is None:
        return {"success": False, "error": f"步骤 {step_id} 不存在"}

    settings = load_provider_settings()
    verify_group = getattr(settings, "verify_model_group", "fast")
    provider = create_provider("openai-compatible", settings, model_group=verify_group)
    if getattr(settings, "verify_model_name", None):
        provider.model_name = settings.verify_model_name

    # Restore the original subject context so prompts, taxonomy and sympy
    # postprocess rules all use the run's original subject. Use a context
    # manager so the threading.local value is restored on exit and does
    # not bleed into the next coroutine on this async thread.
    subject_id = (run_data.get("run_meta") or {}).get("subject_id") or DEFAULT_PROCESSING_SUBJECT
    if not subject_id or subject_id not in VALID_SUBJECTS:
        subject_id = "calculus"
    from stem_tutor.prompts.templates import active_subject_scope
    with active_subject_scope(subject_id):
        problem_text = raw["problem_input"]["problem_text"]
        ref = raw.get("reference_solution", {})
        ref_text = ref.get("reference_text", "")
        assertions = ref.get("key_assertions", [])
        all_steps = steps_raw
        prev_text = all_steps[target_idx - 1].get("normalized_text", "") if target_idx > 0 else ""
        next_text = all_steps[target_idx + 1].get("normalized_text", "") if target_idx < len(all_steps) - 1 else ""
        full_solution = "\n".join(f"{s.get('step_id','')}: {s.get('normalized_text','')}" for s in all_steps)

        from stem_tutor.nodes.verify_steps import (
            _strategy_sympy_verify, _strategy_numerical_verify,
            _strategy_agent_verify, _strategy_pure_llm_verify,
            _outcome_to_verification_result, _rule_based_adjustment,
            _extract_reference_answer_hint, _is_tool_calling_enabled,
        )
        from stem_tutor.graph.strategy import StrategyChain
        from stem_tutor.graph.budget import NodeBudgetConfig, NodeBudgetManager

        reference_answer_hint = _extract_reference_answer_hint(ref_text, assertions)

        unlim_config = NodeBudgetConfig(600, 120, 5, {"simple": 15, "moderate": 25, "complex": 35})
        budget = NodeBudgetManager(config=unlim_config)

        strategies = [
            ("sympy", _strategy_sympy_verify),
            ("numerical", _strategy_numerical_verify),
        ]
        if _is_tool_calling_enabled():
            strategies.append(("tool_agent", _strategy_agent_verify))
        strategies.append(("pure_llm", _strategy_pure_llm_verify))

        chain = StrategyChain(strategies, budget)
        outcome = chain.execute(
            step_text=target.get("normalized_text", ""),
            prev_text=prev_text,
            next_text=next_text,
            reference_text=ref_text,
            reference_answer_hint=reference_answer_hint,
            problem_text=problem_text,
            full_solution=full_solution,
            step_id=step_id,
            total_steps=len(all_steps),
            assertions=assertions,
            final_answer_status="",
            computation_hints="",
            provider=provider,
            subject_id=subject_id,
        )

        from stem_tutor.domain.models import VerificationResult
        result = _outcome_to_verification_result(outcome, step_id)

        adj_label, adj_evidence, adj_principles = _rule_based_adjustment(target.get("normalized_text", ""), subject_id)
        if adj_label is not None:
            result = VerificationResult(
                step_id=result.step_id,
                label=adj_label,
                evidence=adj_evidence or result.evidence,
                confidence=min(result.confidence, 0.6),
                violated_principles=sorted(set(result.violated_principles + adj_principles)),
                sympy_verified=result.sympy_verified,
                sympy_equivalent=result.sympy_equivalent,
            )

        await _update_step_in_run(run_id, user_id, step_id, result)

        return {
            "success": True,
            "verification_result": result.model_dump(),
            "elapsed_seconds": round(budget.elapsed(), 2),
        }


async def regenerate_reference(run_id: str, user_id: int):
    """SSE stream that re-runs the reference solution for an existing run
    and persists the new value to the database. Mirrors the schema of
    practice_reference_stream so the client can reuse _readReferenceSSE.
    """
    import asyncio
    import json as _json
    from stem_tutor.nodes.generate_reference_solution import _generate_via_agent, _is_degraded
    from stem_tutor.prompts.templates import active_subject_scope

    run_data = await _load_run_result(run_id, user_id)
    if not run_data:
        yield f"event: reference_progress\ndata: {_json.dumps({'type':'error','message':'运行记录不存在'}, ensure_ascii=False)}\n\n"
        return

    raw = run_data.get("raw_output", {})
    problem_text = raw.get("problem_input", {}).get("problem_text", "")
    if not problem_text:
        yield f"event: reference_progress\ndata: {_json.dumps({'type':'error','message':'题目信息缺失'}, ensure_ascii=False)}\n\n"
        return

    subject_id = (run_data.get("run_meta") or {}).get("subject_id") or "calculus"
    if not subject_id or subject_id not in VALID_SUBJECTS:
        subject_id = "calculus"

    yield f"event: reference_progress\ndata: {_json.dumps({'type':'progress','message':'正在重新生成参考解答...'}, ensure_ascii=False)}\n\n"

    loop = asyncio.get_event_loop()
    try:
        with active_subject_scope(subject_id):
            new_ref, tool_calls = await loop.run_in_executor(
                None,
                lambda: _generate_via_agent(problem_text, subject_id=subject_id),
            )
        ref_text = new_ref.get("reference_text", "")
        if not ref_text or _is_degraded(ref_text):
            raise ValueError(
                f"Generated reference is degraded: len={len(ref_text)}, "
                f"meta_thinking={_is_degraded(ref_text)}"
            )
    except Exception as exc:
        logger.error("[RegenRef] failed: %s", exc, exc_info=True)
        yield f"event: reference_progress\ndata: {_json.dumps({'type':'error','message':'重新生成失败，请稍后重试'}, ensure_ascii=False)}\n\n"
        return

    new_ref_clean = new_ref if isinstance(new_ref, dict) else (
        new_ref.model_dump() if hasattr(new_ref, "model_dump") else {}
    )
    run_data["reference_solution"] = new_ref_clean
    raw["reference_solution"] = new_ref_clean
    run_data.setdefault("reference_history", []).append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tool_call_count": len(tool_calls),
    })
    await database.update_run(run_id, run_data)

    yield f"event: reference_progress\ndata: {_json.dumps({'type':'result','reference_text':ref_text,'key_assertions':new_ref_clean.get('key_assertions',[]),'tool_call_count':len(tool_calls)}, ensure_ascii=False)}\n\n"


async def regenerate_review_problems(run_id: str, user_id: int):
    """SSE stream that re-rolls the review problems for an existing run
    and persists the new list. Mirrors regenerate_reference so the frontend
    can use a similar fetch + re-render pattern.
    """
    import asyncio
    import json as _json
    from stem_tutor.nodes.generate_review_problems import _generate_review_problems_inner
    from stem_tutor.prompts.templates import active_subject_scope, set_active_subject
    from stem_tutor.domain.models import (
        ProblemInput, SolutionStep, ErrorDiagnosis, VerificationResult, FeedbackReport,
    )
    from stem_tutor.providers.factory import create_provider
    from stem_tutor.graph.observability import record_provider_call
    from stem_tutor.domain.models import ReviewProblem

    run_data = await _load_run_result(run_id, user_id)
    if not run_data:
        yield f"event: review_progress\ndata: {_json.dumps({'type':'error','message':'运行记录不存在'}, ensure_ascii=False)}\n\n"
        return

    raw = run_data.get("raw_output", {})
    problem_input_dict = raw.get("problem_input", {})
    if not problem_input_dict.get("problem_text"):
        yield f"event: review_progress\ndata: {_json.dumps({'type':'error','message':'题目信息缺失'}, ensure_ascii=False)}\n\n"
        return

    subject_id = (run_data.get("run_meta") or {}).get("subject_id") or "calculus"
    if not subject_id or subject_id not in VALID_SUBJECTS:
        subject_id = "calculus"

    yield f"event: review_progress\ndata: {_json.dumps({'type':'progress','message':'正在重新生成复习题...'}, ensure_ascii=False)}\n\n"

    # Reconstruct a minimal state dict the inner function can consume.
    def _coerce_diagnoses(items):
        out = []
        for d in items or []:
            if isinstance(d, dict):
                try:
                    out.append(ErrorDiagnosis(**d))
                except Exception:
                    pass
            elif hasattr(d, "model_dump"):
                out.append(d)
        return out

    def _coerce_steps(items):
        out = []
        for s in items or []:
            if isinstance(s, dict):
                try:
                    out.append(SolutionStep(**s))
                except Exception:
                    pass
            elif hasattr(s, "model_dump"):
                out.append(s)
        return out

    def _coerce_verifications(items):
        out = []
        for v in items or []:
            if isinstance(v, dict):
                try:
                    out.append(VerificationResult(**v))
                except Exception:
                    pass
            elif hasattr(v, "model_dump"):
                out.append(v)
        return out

    try:
        problem_input = ProblemInput(**problem_input_dict)
    except Exception:
        problem_input = ProblemInput(problem_id=run_id, problem_text=problem_input_dict.get("problem_text", ""))

    fake_state = {
        "problem_input": problem_input,
        "reference_solution": raw.get("reference_solution", {}),
        "diagnosis_results": _coerce_diagnoses(raw.get("diagnosis_results", [])),
        "normalized_steps": _coerce_steps(raw.get("normalized_steps", [])),
        "verification_results": _coerce_verifications(raw.get("verification_results", [])),
        "uncertainty_flags": list(run_data.get("uncertainty_flags", [])),
        "run_meta": dict(run_data.get("run_meta", {})),
        "subject_id": subject_id,
    }

    loop = asyncio.get_event_loop()
    settings = load_provider_settings()
    try:
        provider = create_provider(settings.provider_type, settings, model_group="fast")
    except Exception as exc:
        logger.error("[RegenReview] failed to create provider: %s", exc)
        yield f"event: review_progress\ndata: {_json.dumps({'type':'error','message':'服务暂时不可用'}, ensure_ascii=False)}\n\n"
        return

    try:
        with active_subject_scope(subject_id):
            inner_result = await loop.run_in_executor(
                None, lambda: _generate_review_problems_inner(fake_state, provider)
            )
    except Exception as exc:
        logger.error("[RegenReview] failed: %s", exc, exc_info=True)
        yield f"event: review_progress\ndata: {_json.dumps({'type':'error','message':'重新生成失败'}, ensure_ascii=False)}\n\n"
        return

    new_problems = inner_result.get("review_problems", [])
    if hasattr(new_problems[0] if new_problems else None, "model_dump"):
        new_problems_dicts = [p.model_dump() for p in new_problems]
    elif isinstance(new_problems[0] if new_problems else None, dict):
        new_problems_dicts = list(new_problems)
    else:
        new_problems_dicts = []
    if not new_problems_dicts:
        yield f"event: review_progress\ndata: {_json.dumps({'type':'error','message':'生成结果为空'}, ensure_ascii=False)}\n\n"
        return

    run_data["review_problems"] = new_problems_dicts
    raw["review_problems"] = new_problems_dicts
    run_data.setdefault("review_history", []).append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "problem_count": len(new_problems_dicts),
    })
    await database.update_run(run_id, run_data)

    yield f"event: review_progress\ndata: {_json.dumps({'type':'result','problems':new_problems_dicts}, ensure_ascii=False)}\n\n"


async def get_stats(user_id: int) -> dict:
    all_rows = await database.get_all_runs_for_stats(user_id)
    if not all_rows:
        return {
            "total_runs": 0, "success_count": 0, "failed_count": 0,
            "success_rate": 0, "avg_duration_seconds": 0,
            "today_runs": 0, "week_runs": 0, "streak_days": 0,
            "subject_distribution": {}, "daily_trend": [],
        }

    from datetime import datetime, timezone, timedelta

    total = 0
    success = 0
    failed = 0
    review = 0
    durations = []
    subject_dist = {}
    daily_counts = {}
    date_set = set()

    now = datetime.now(timezone.utc)
    today_str = now.strftime("%Y-%m-%d")
    week_ago = now - timedelta(days=7)

    for row in all_rows:
        data = row["data"]
        meta = data.get("run_meta", {})
        run_status = data.get("user_status") or data.get("status", "unknown")
        if run_status == "success":
            run_status = "complete"
        elif run_status == "manual_review_required":
            run_status = "needs_review"
        elif run_status == "failed":
            run_status = "unavailable"

        total += 1
        if run_status == "complete":
            success += 1
        elif run_status == "unavailable":
            failed += 1
        elif run_status == "needs_review":
            review += 1

        started = meta.get("started_at", "")
        completed = meta.get("completed_at", "")
        if started and completed:
            try:
                st = datetime.fromisoformat(started)
                ct = datetime.fromisoformat(completed)
                durations.append(int((ct - st).total_seconds()))
            except Exception:
                pass

        subject_id = meta.get("subject_id", "unknown")
        try:
            from stem_tutor.subjects.context import get_subject_context
            ctx = get_subject_context(subject_id)
            subject_key = ctx.display_name
        except Exception:
            subject_key = subject_id
        subject_dist[subject_key] = subject_dist.get(subject_key, 0) + 1

        day_key = (completed or started)[:10]
        if day_key:
            daily_counts[day_key] = daily_counts.get(day_key, 0) + 1
            date_set.add(day_key)

    avg_duration = sum(durations) // len(durations) if durations else 0
    success_rate = success / total if total > 0 else 0

    today_runs = daily_counts.get(today_str, 0)
    week_runs = sum(
        count for date_str, count in daily_counts.items()
        if date_str >= week_ago.strftime("%Y-%m-%d")
    )

    streak = 0
    check = now.date()
    for _ in range(365):
        ds = check.strftime("%Y-%m-%d")
        if ds in date_set:
            streak += 1
            check -= timedelta(days=1)
        else:
            break

    sorted_days = sorted(daily_counts.items())
    daily_trend = [{"date": d, "count": c} for d, c in sorted_days[-60:]]

    return {
        "total_runs": total,
        "success_count": success,
        "failed_count": failed,
        "review_count": review,
        "success_rate": round(success_rate, 4),
        "avg_duration_seconds": avg_duration,
        "today_runs": today_runs,
        "week_runs": week_runs,
        "streak_days": streak,
        "subject_distribution": subject_dist,
        "daily_trend": daily_trend,
    }



async def _load_chat_history(run_id: str, user_id: int) -> list[dict]:
    return await database.load_chat(run_id, user_id)


async def _save_chat_history(run_id: str, user_id: int, messages: list[dict]) -> None:
    await database.save_chat(run_id, user_id, messages)


async def _build_chat_context(run_id: str, user_id: int) -> str:
    result = await _load_run_result(run_id, user_id)
    if result is None:
        return ""

    raw = result.get("raw_output", {})
    problem_input = raw.get("problem_input", {})

    lines = ["以下是学生之前提交的题目和系统分析结果：", ""]

    problem_text = problem_input.get("problem_text") or "（题目信息缺失）"
    lines.append(f"【题目】\n{problem_text}")
    lines.append("")

    steps = result.get("steps", [])
    if steps:
        lines.append("【步骤验证结果】")
        for s in steps:
            step_id = s.get("step_id", "?")
            raw_text = s.get("raw_text", "")[:80]
            label = s.get("label", "unclear")
            conf = s.get("confidence", 0.0)
            lines.append(f"步骤 {step_id}: {raw_text} → {label}（置信度 {conf*100:.0f}%）")
        lines.append("")

    diagnoses = result.get("diagnoses", [])
    if diagnoses:
        lines.append("【错误诊断】")
        for d in diagnoses:
            step_id = d.get("step_id", "?")
            err_code = d.get("error_code", "")
            category = d.get("category", "")
            root_cause = d.get("root_cause_hypothesis", "")
            lines.append(f"步骤 {step_id}: {err_code} - {category}")
            if root_cause:
                lines.append(f"  原因: {root_cause}")
        lines.append("")

    concise_summary = result.get("concise_summary", "")
    likely_cause = result.get("likely_cause")
    next_action = result.get("next_action")
    review_concepts = result.get("review_concepts", [])

    if concise_summary:
        lines.append("【学习反馈】")
        lines.append(f"摘要: {concise_summary}")
        if likely_cause:
            lines.append(f"可能原因: {likely_cause}")
        if next_action:
            lines.append(f"建议行动: {next_action}")
        if review_concepts:
            lines.append(f"需复习概念: {', '.join(review_concepts)}")
        lines.append("")

    return "\n".join(lines)


async def chat_stream(
    run_id: str,
    user_id: int,
    user_message: str,
    model_name: str = "DeepSeek-V3.2",
    provider_name: str = "openai-compatible",
):
    import json as _json

    result = await _load_run_result(run_id, user_id)
    if result is None:
        yield f"data: {_json.dumps({'type': 'chat_error', 'message': '分析结果不存在'}, ensure_ascii=False)}\n\n"
        return

    settings = load_provider_settings()

    # Resolve the original subject from the stored run so the chat tutor
    # speaks with the right subject identity (e.g. "量子物理" not "微积分").
    run_meta = result.get("run_meta") or {}
    subject_id = run_meta.get("subject_id") or DEFAULT_PROCESSING_SUBJECT
    if not subject_id or subject_id not in VALID_SUBJECTS:
        subject_id = "calculus"
    from stem_tutor.subjects.context import get_subject_context
    try:
        subject_display = get_subject_context(subject_id).display_name
    except Exception:
        subject_display = "数理基础"

    context = await _build_chat_context(run_id, user_id)
    system_prompt = f"""你是一位{subject_display}辅导老师。你的职责是：
1. 基于系统分析结果回答学生的追问
2. 用清晰、通俗的语言解释相关概念
3. 如果涉及具体步骤，引用步骤编号（如"第2步"）
4. 适当给出类似题目或解题方法建议
5. 使用 $...$ 包裹行内公式，$$...$$ 包裹独立公式
6. 所有回答使用中文

{context}"""

    history = await _load_chat_history(run_id, user_id)

    user_ts = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    history.append({"role": "user", "content": user_message, "ts": user_ts})

    messages = [
        {"role": "system", "content": system_prompt},
    ]
    for m in history[:-1]:
        messages.append({"role": m.get("role", "user"), "content": m.get("content", "")})
    messages.append({"role": "user", "content": user_message})

    url = f"{settings.base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model_name,
        "messages": messages,
        "temperature": 0.5,
        "max_tokens": 8192,
        "stream": True,
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=settings.timeout_seconds, stream=True)
        resp.raise_for_status()

        full_content = ""
        full_reasoning = ""
        truncated = False
        for line in resp.iter_lines():
            line = line.decode("utf-8")
            if not line.startswith("data:"):
                continue
            data_str = line[5:].strip()
            if data_str == "[DONE]":
                break
            try:
                chunk_data = json.loads(data_str)
                choices = chunk_data.get("choices", [{}])
                delta = choices[0].get("delta", {})
                finish_reason = choices[0].get("finish_reason")
                if finish_reason == "length":
                    truncated = True
                reasoning = delta.get("reasoning_content", "")
                if reasoning:
                    full_reasoning += reasoning
                    yield f"data: {_json.dumps({'type': 'chat_thinking', 'content': reasoning}, ensure_ascii=False)}\n\n"
                content = delta.get("content", "")
                if content:
                    full_content += content
                    yield f"data: {_json.dumps({'type': 'chat_chunk', 'content': content}, ensure_ascii=False)}\n\n"
            except json.JSONDecodeError:
                continue

        if truncated:
            warning = "\n\n⚠ 回答已被 token 限制截断。请分次提问，或更换更长上下文的模型。"
            full_content += warning
            yield f"data: {_json.dumps({'type': 'chat_chunk', 'content': warning}, ensure_ascii=False)}\n\n"

        assistant_ts = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
        history.append({"role": "assistant", "content": full_content, "ts": assistant_ts, "model": model_name})
        await _save_chat_history(run_id, user_id, history)

        yield f"data: {_json.dumps({'type': 'chat_done', 'message': '回复完成'}, ensure_ascii=False)}\n\n"

    except Exception as exc:
        logger.error("[Chat] event_generator error: %s", exc, exc_info=True)
        yield f"data: {_json.dumps({'type': 'chat_error', 'message': '对话出错，请稍后重试'}, ensure_ascii=False)}\n\n"


_SKILL_CATEGORY_MAP = {
    "Rule Application Errors": "规则应用",
    "Algebraic Manipulation Errors": "代数运算",
    "Theorem/Condition Misuse": "定理/条件",
    "Conceptual Confusion": "概念理解",
    "Reasoning Quality Issues": "逻辑推理",
    "Dimension Errors": "维度分析",
    "Computational Errors": "计算过程",
    "Spectral Theory Errors": "谱理论",
    "Structural Property Errors": "结构性质",
    "Vector Space Errors": "向量空间",
}


def _empty_report(days: int = 0) -> dict:
    return {
        "time_range": {"start": "", "end": "", "days": days},
        "total_runs": 0,
        "error_frequency": [],
        "radar_data": {},
        "heatmap_data": {"skills": [], "subjects": [], "matrix": []},
        "error_evolution": [],
        "improvement_signals": [],
        "taxonomy_summary": {},
    }


async def get_report_run_list(user_id: int) -> list[dict]:
    all_rows = await database.get_all_runs_for_stats(user_id)
    if not all_rows:
        return []

    runs = []
    for row in all_rows:
        data = row["data"]
        meta = data.get("run_meta", {})
        run_id = meta.get("run_id", row.get("id", ""))
        subject_id = meta.get("subject_id", DEFAULT_DISPLAY_SUBJECT) or row.get("subject", "")
        started = meta.get("started_at", "")
        completed = meta.get("completed_at", "")
        timestamp = completed or started or row.get("created_at", "")

        display_name = subject_id
        try:
            ctx = get_subject_context(subject_id)
            display_name = ctx.display_name
        except Exception:
            pass

        raw = data.get("raw_output", {})
        problem_input = raw.get("problem_input", {})
        problem_text = ""
        if isinstance(problem_input, dict):
            problem_text = problem_input.get("problem_text", "")
        if not problem_text:
            problem_text = row.get("problem_text", "") or meta.get("problem_text", "")

        has_errors = bool(data.get("diagnoses"))
        run_status = data.get("user_status") or data.get("status", "unknown")

        runs.append({
            "run_id": run_id,
            "subject_id": subject_id,
            "subject_display": display_name,
            "timestamp": timestamp,
            "problem_preview": (problem_text[:80] + "...") if len(problem_text) > 80 else problem_text,
            "status": run_status,
            "has_errors": has_errors,
        })

    runs.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    return runs


async def get_report_data(
    user_id: int,
    days: int = 30,
    start_date: str | None = None,
    end_date: str | None = None,
    run_ids: list[str] | None = None,
) -> dict:
    from datetime import timedelta

    all_rows = await database.get_all_runs_for_stats(user_id)
    if not all_rows:
        return _empty_report(days)

    now = datetime.now(timezone.utc)
    cutoff = None
    start_dt = None
    end_dt = None

    if run_ids:
        id_set = set(run_ids)
    elif start_date or end_date:
        if start_date:
            try:
                start_dt = datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc)
            except Exception:
                start_dt = None
        if end_date:
            try:
                end_dt = datetime.fromisoformat(end_date).replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
            except Exception:
                end_dt = None
    elif days > 0:
        cutoff = now - timedelta(days=days)

    runs_data: list[dict] = []
    for row in all_rows:
        data = row["data"]
        meta = data.get("run_meta", {})
        run_id = meta.get("run_id", "")
        completed = meta.get("completed_at", "")
        started = meta.get("started_at", "")

        if run_ids:
            if run_id not in id_set:
                continue
        else:
            ts_str = completed or started
            if ts_str:
                try:
                    ts = datetime.fromisoformat(ts_str)
                except Exception:
                    ts = None
            else:
                ts = None

            if cutoff and (not ts or ts < cutoff):
                continue
            if start_dt and (not ts or ts < start_dt):
                continue
            if end_dt and (not ts or ts > end_dt):
                continue

        runs_data.append(data)

    runs_data.sort(key=lambda d: (d.get("run_meta", {}).get("completed_at") or d.get("run_meta", {}).get("started_at", "")))

    if not runs_data:
        return _empty_report(days)

    first_ts = runs_data[0].get("run_meta", {}).get("started_at", "")[:10]
    last_ts = runs_data[-1].get("run_meta", {}).get("completed_at", "")[:10]

    error_counts: dict[str, dict] = {}
    subject_errors: dict[str, dict[str, int]] = {}
    skill_subject_matrix: dict[str, dict[str, list[float]]] = {}
    subject_display_map: dict[str, str] = {}
    taxonomy_summary: dict[str, str] = {}
    period_errors: dict[str, dict[str, int]] = {}
    period_run_counts: dict[str, int] = {}

    for data in runs_data:
        meta = data.get("run_meta", {})
        subject_id = meta.get("subject_id", "unknown")
        try:
            ctx = get_subject_context(subject_id)
            subject_key = ctx.display_name
        except Exception:
            subject_key = subject_id
        subject_display_map[subject_id] = subject_key

        diagnoses = data.get("diagnoses", [])
        steps = data.get("steps", [])

        for d in diagnoses:
            error_code = d.get("error_code", "UNKNOWN")
            category_en = d.get("category", "")
            category_zh = _SKILL_CATEGORY_MAP.get(category_en, None)
            if not category_zh:
                continue

            entry = lookup_error(error_code, subject_id=subject_id if subject_id != "unknown" else "calculus")
            if entry and error_code not in taxonomy_summary:
                taxonomy_summary[error_code] = entry.short_desc

            if error_code not in error_counts:
                error_counts[error_code] = {
                    "error_code": error_code,
                    "category": category_zh,
                    "count": 0,
                    "runs_involved": 0,
                    "_run_ids": set(),
                }
            error_counts[error_code]["count"] += 1
            run_id = data.get("run_meta", {}).get("run_id", "")
            if run_id and run_id not in error_counts[error_code]["_run_ids"]:
                error_counts[error_code]["_run_ids"].add(run_id)
                error_counts[error_code]["runs_involved"] += 1

            if subject_key not in subject_errors:
                subject_errors[subject_key] = {}
            subject_errors[subject_key][category_zh] = subject_errors[subject_key].get(category_zh, 0) + 1

            if category_zh not in skill_subject_matrix:
                skill_subject_matrix[category_zh] = {}
            if subject_key not in skill_subject_matrix[category_zh]:
                skill_subject_matrix[category_zh][subject_key] = []
            skill_subject_matrix[category_zh][subject_key].append(1)

        for s in steps:
            label = s.get("label", "")
            if label in ("correct", "unverified"):
                continue
            vp_list = s.get("violated_principles", [])
            for vp in vp_list:
                vp_zh = _SKILL_CATEGORY_MAP.get(vp, None)
                if not vp_zh:
                    continue
                if vp_zh not in skill_subject_matrix:
                    skill_subject_matrix[vp_zh] = {}
                if subject_key not in skill_subject_matrix[vp_zh]:
                    skill_subject_matrix[vp_zh][subject_key] = []
                skill_subject_matrix[vp_zh][subject_key].append(1)

        completed_str = meta.get("completed_at", "") or meta.get("started_at", "")
        if completed_str:
            try:
                dt = datetime.fromisoformat(completed_str)
                iso_cal = dt.isocalendar()
                period_label = f"{iso_cal[0]}-W{iso_cal[1]:02d}"
            except Exception:
                period_label = "unknown"
        else:
            period_label = "unknown"

        period_run_counts[period_label] = period_run_counts.get(period_label, 0) + 1
        if period_label not in period_errors:
            period_errors[period_label] = {}
        for d in diagnoses:
            cat = _SKILL_CATEGORY_MAP.get(d.get("category", ""), None)
            if not cat:
                continue
            period_errors[period_label][cat] = period_errors[period_label].get(cat, 0) + 1

    error_frequency = sorted(
        [{"error_code": v["error_code"], "category": v["category"], "count": v["count"], "runs_involved": v["runs_involved"]} for v in error_counts.values()],
        key=lambda x: x["count"],
        reverse=True,
    )

    skills_list = sorted(skill_subject_matrix.keys())
    subjects_list = sorted({k for sub_dict in skill_subject_matrix.values() for k in sub_dict.keys()})
    matrix = []
    for sk in skills_list:
        row = []
        for subj in subjects_list:
            vals = skill_subject_matrix.get(sk, {}).get(subj, [])
            if vals:
                total_in_period = period_run_counts.get(subj, 1)
                ratio = len(vals) / max(total_in_period, 1)
                row.append(round(max(0.0, 1.0 - ratio), 2))
            else:
                row.append(1.0)
        matrix.append(row)

    error_evolution = []
    for period_label in sorted(period_errors.keys()):
        dist = period_errors[period_label]
        total_e = sum(dist.values())
        distribution = {k: round(v / total_e, 3) for k, v in dist.items()} if total_e > 0 else {}
        error_evolution.append({"period": period_label, "distribution": distribution})

    improvement_signals = []
    if len(error_evolution) >= 2:
        first_half = error_evolution[: len(error_evolution) // 2]
        second_half = error_evolution[len(error_evolution) // 2 :]
        first_cats: dict[str, int] = {}
        second_cats: dict[str, int] = {}
        for p in first_half:
            for cat, ratio in p["distribution"].items():
                first_cats[cat] = first_cats.get(cat, 0) + ratio
        for p in second_half:
            for cat, ratio in p["distribution"].items():
                second_cats[cat] = second_cats.get(cat, 0) + ratio
        for cat in first_cats:
            if cat not in second_cats and first_cats[cat] > 0:
                improvement_signals.append({
                    "type": "error_eliminated",
                    "description": f"「{cat}」类错误在前半段存在，但后半段已不再出现",
                    "subject": "all",
                })
            elif cat in second_cats and first_cats[cat] > 0:
                drop = (first_cats[cat] - second_cats[cat]) / first_cats[cat]
                if drop >= 0.3:
                    improvement_signals.append({
                        "type": "frequency_drop",
                        "description": f"「{cat}」类错误频率下降了 {drop * 100:.0f}%",
                        "subject": "all",
                    })

    all_subjects = sorted(subject_display_map.values())
    for subj in all_subjects:
        subj_runs = [d for d in runs_data if subject_display_map.get(d.get("run_meta", {}).get("subject_id", DEFAULT_DISPLAY_SUBJECT), "") == subj]
        if len(subj_runs) >= 3:
            last_n = subj_runs[-3:]
            has_error = False
            for r in last_n:
                if r.get("diagnoses"):
                    has_error = True
                    break
            if not has_error:
                improvement_signals.append({
                    "type": "streak",
                    "description": f"在「{subj}」中连续 {len(last_n)} 次诊断未检测到错误",
                    "subject": subj,
                })

    mastery_data = await database.get_mastery(user_id)
    mastery_errors = mastery_data.get("errors", {})
    mastery_summary = {
        "total_error_types": len(mastery_errors),
        "mastered_count": sum(1 for e in mastery_errors.values() if e.get("mastered") or e.get("auto_mastered")),
        "mastered_items": [
            {"error_code": code, "total_encounters": e.get("total", 0)}
            for code, e in mastery_errors.items()
            if e.get("mastered") or e.get("auto_mastered")
        ],
        "learning_items": [
            {"error_code": code, "total_encounters": e.get("total", 0), "last_seen": e.get("last_seen", "")}
            for code, e in mastery_errors.items()
            if not e.get("mastered") and not e.get("auto_mastered") and e.get("total", 0) >= 2
        ],
        "recent_analysis_count": len(mastery_data.get("analysis_history", [])[-10:]),
    }

    return {
        "time_range": {
            "start": first_ts,
            "end": last_ts,
            "days": days,
            "start_date": start_date or "",
            "end_date": end_date or "",
            "run_count": len(run_ids) if run_ids else 0,
        },
        "total_runs": len(runs_data),
        "error_frequency": error_frequency,
        "radar_data": subject_errors,
        "heatmap_data": {"skills": skills_list, "subjects": subjects_list, "matrix": matrix},
        "error_evolution": error_evolution,
        "improvement_signals": improvement_signals,
        "taxonomy_summary": taxonomy_summary,
        "mastery_summary": mastery_summary,
    }


async def _save_report(report_id: str, user_id: int, report_data: dict, sections: list, metadata: dict) -> None:
    import logging
    logger = logging.getLogger(__name__)
    payload = {
        "report_id": report_id,
        "title": metadata.get("title", f"学习报告 · {metadata.get('created_at', '')[:10]}"),
        "model": metadata.get("model", ""),
        "created_at": metadata.get("created_at", ""),
        "filter": metadata.get("filter", {}),
        "report_data": report_data,
        "sections": sections,
    }
    await database.save_report(report_id, user_id, payload)
    logger.info("[Report] Saved report %s to database", report_id)


async def _load_report(report_id: str, user_id: int) -> dict | None:
    row = await database.load_report(report_id, user_id)
    if not row:
        return None
    return row["data"]


async def list_reports(user_id: int, page: int = 1, per_page: int = 20) -> dict:
    all_rows = await database.list_reports_db(user_id)
    reports = []
    for row in all_rows:
        data = row["data"]
        reports.append({
            "report_id": data.get("report_id", row.get("id", "")),
            "title": data.get("title", "未命名报告"),
            "model": data.get("model", ""),
            "created_at": data.get("created_at", ""),
            "total_runs": (data.get("filter") or {}).get("total_runs", 0),
            "section_count": len(data.get("sections", [])),
        })

    total = len(reports)
    start = (page - 1) * per_page
    return {
        "reports": reports[start:start + per_page],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


async def delete_reports(user_id: int, report_ids: list[str]) -> dict:
    deleted = 0
    not_found = []
    for rid in report_ids:
        ok = await database.delete_report_db(rid, user_id)
        if ok:
            deleted += 1
        else:
            not_found.append(rid)
    return {"deleted": deleted, "not_found": not_found}


async def practice_verify_stream(
    problem_text: str,
    student_solution: str,
    subject_id: str = "calculus",
    related_weakness_code: str = "",
):
    import json as _json

    from stem_tutor.subjects.context import get_subject_context
    from stem_tutor.prompts.templates import set_active_subject, active_subject_scope

    if not subject_id or subject_id not in VALID_SUBJECTS:
        subject_id = "calculus"
    set_active_subject(subject_id)

    try:
        subject_display = get_subject_context(subject_id).display_name
    except Exception:
        subject_display = "数学/物理"

    settings = load_provider_settings()
    model = settings.resolve_model_name("fast")
    url = f"{settings.base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {settings.api_key}", "Content-Type": "application/json"}

    yield f"event: practice_progress\ndata: {_json.dumps({'type': 'progress', 'node': 'verify', 'message': '正在验证解答...'}, ensure_ascii=False)}\n\n"

    prompt = (
        f"你是一位严格的{subject_display}教师。请判断学生的解答是否正确。\n\n"
        f"题目：{problem_text}\n\n"
        f"学生解答：{student_solution}\n\n"
        "请按以下 JSON 格式回复（不要有其他文字）：\n"
        '{"all_correct": true/false, '
        '"summary": "一句话总结", '
        '"hint": "如果不正确，给出提示；如果正确则为null", '
        '"step_results": [{"text": "步骤内容", "label": "correct或incorrect_math"}]}\n'
        "step_results 是按行拆分学生解答后每步的对错判断。"
    )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是一位精准的JSON API，只输出合法JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 1500,
    }

    try:
        import re as _re
        from stem_tutor.providers.openai_compatible_provider import _fix_json_escapes, _fix_json_control_chars
        resp = requests.post(url, json=payload, headers=headers, timeout=60)
        resp.raise_for_status()
        body = resp.json()
        content = body["choices"][0]["message"]["content"].strip()
        json_match = _re.search(r'\{[\s\S]*\}', content)
        raw = json_match.group() if json_match else content
        raw = _fix_json_control_chars(raw)
        raw = _fix_json_escapes(raw)
        parsed = _json.loads(raw)

        result = {
            "type": "result",
            "label": "correct" if parsed.get("all_correct") else "incorrect_math",
            "summary": parsed.get("summary", ""),
            "step_results": parsed.get("step_results", []),
            "hint": parsed.get("hint"),
            "all_correct": bool(parsed.get("all_correct")),
        }
    except Exception as exc:
        logger.error("[Practice] LLM verify error: %s", exc, exc_info=True)
        result = {
            "type": "result",
            "label": "uncertain",
            "summary": "验证完成",
            "step_results": [],
            "hint": None,
            "all_correct": False,
        }
        yield f"event: practice_progress\ndata: {_json.dumps({'type': 'error', 'message': 'LLM 验证出错，默认标记为正确'}, ensure_ascii=False)}\n\n"

    yield f"event: practice_progress\ndata: {_json.dumps(result, ensure_ascii=False)}\n\n"


async def practice_reference_stream(problem_text: str, subject_id: str = "calculus"):
    import asyncio
    import json as _json

    from stem_tutor.nodes.generate_reference_solution import _generate_via_agent
    from stem_tutor.prompts.templates import active_subject_scope

    if not subject_id or subject_id not in VALID_SUBJECTS:
        subject_id = "calculus"

    yield f"event: reference_progress\ndata: {_json.dumps({'type': 'progress', 'message': '正在生成参考解答（使用工具验证）...'}, ensure_ascii=False)}\n\n"

    loop = asyncio.get_event_loop()

    from stem_tutor.nodes.generate_reference_solution import _is_degraded

    try:
        with active_subject_scope(subject_id):
            raw, tool_calls = await loop.run_in_executor(
                None,
                lambda: _generate_via_agent(problem_text, subject_id=subject_id),
            )
        ref_text = raw.get("reference_text", "")
        if not ref_text or _is_degraded(ref_text):
            raise ValueError(
                f"Generated reference is degraded or empty: len={len(ref_text)}, "
                f"meta_thinking={_is_degraded(ref_text)}"
            )
        result = {
            "type": "result",
            "reference_text": ref_text,
            "key_assertions": raw.get("key_assertions", []),
            "tool_call_count": len(tool_calls),
        }
    except Exception as exc:
        logger.error("[Practice] Reference generation failed: %s", exc, exc_info=True)
        result = {
            "type": "result",
            "reference_text": "参考解答暂时无法生成，请稍后重试。",
            "key_assertions": [],
            "tool_call_count": 0,
        }
        yield f"event: reference_progress\ndata: {_json.dumps({'type': 'error', 'message': '参考解答生成异常'}, ensure_ascii=False)}\n\n"
        return

    yield f"event: reference_progress\ndata: {_json.dumps(result, ensure_ascii=False)}\n\n"


async def report_stream(user_id: int, data: dict, model_name: str = "qwen/qwen3.6-plus"):
    import asyncio
    import json as _json

    from stem_tutor.prompts.templates import report_prompt

    yield f"data: {_json.dumps({'type': 'report_progress', 'message': '正在准备数据并调用 AI 模型...'}, ensure_ascii=False)}\n\n"

    settings = load_provider_settings()
    prompt = report_prompt(
        aggregated_data={
            "error_frequency": data.get("error_frequency", []),
            "radar_data": data.get("radar_data", {}),
            "heatmap_data": data.get("heatmap_data", {}),
            "error_evolution": data.get("error_evolution", []),
            "improvement_signals": data.get("improvement_signals", []),
        },
        time_range=data.get("time_range", {"start": "?", "end": "?", "days": 0}),
        total_runs=data.get("total_runs", 0),
        taxonomy_summary=data.get("taxonomy_summary", {}),
    )

    url = f"{settings.base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": "你是一位专业的 STEM 学习分析师，擅长从诊断数据中生成个性化学习报告。请严格按照用户要求的 JSON 格式输出。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.4,
        "max_tokens": 12000,
        "stream": True,
    }

    logger.info("[Report] LLM request: model=%s, prompt_len=%d, max_tokens=12000, stream=True", model_name, len(prompt))

    import functools
    max_attempts = 3
    full_content = ""
    succeeded = False
    for attempt in range(1, max_attempts + 1):
        try:
            full_content = ""
            resp = await asyncio.to_thread(
                functools.partial(requests.post, url, json=payload, headers=headers, timeout=300, stream=True)
            )
            if resp.status_code != 200 and attempt < max_attempts and resp.status_code in (429, 500, 502, 503, 504):
                logger.warning("[Report] LLM returned %d on attempt %d/%d, retrying", resp.status_code, attempt, max_attempts)
                yield f"data: {_json.dumps({'type': 'report_retrying', 'attempt': attempt + 1, 'max_attempts': max_attempts, 'message': f'服务器返回 {resp.status_code}，正在自动重试...'}, ensure_ascii=False)}\n\n"
                resp.close()
                await asyncio.sleep(_retry_sleep_seconds(attempt))
                continue
            resp.raise_for_status()

            for line in resp.iter_lines():
                if not line:
                    continue
                line_str = line.decode("utf-8") if isinstance(line, bytes) else line
                if not line_str.startswith("data:"):
                    continue
                data_str = line_str[5:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    chunk_data = _json.loads(data_str)
                    choices = chunk_data.get("choices", [{}])
                    delta = choices[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        full_content += content
                        if len(full_content) % 500 < len(content):
                            yield f"data: {_json.dumps({'type': 'report_progress', 'message': f'AI 模型返回中（已接收 {len(full_content)} 字符）...'}, ensure_ascii=False)}\n\n"
                except _json.JSONDecodeError:
                    continue

            succeeded = True
            break
        except (requests.Timeout, requests.ConnectionError) as exc:
            if attempt < max_attempts and _is_retryable_exception(exc):
                logger.warning("[Report] Attempt %d/%d failed (%s), retrying", attempt, max_attempts, type(exc).__name__)
                yield f"data: {_json.dumps({'type': 'report_retrying', 'attempt': attempt + 1, 'max_attempts': max_attempts, 'message': '网络或模型暂时不稳定，正在自动重试...'}, ensure_ascii=False)}\n\n"
                await asyncio.sleep(_retry_sleep_seconds(attempt))
                continue
            logger.error("[Report] Generation failed after %d attempt(s): %s", attempt, exc, exc_info=True)
            yield f"data: {_json.dumps({'type': 'report_error', 'message': '生成失败，请稍后重试'}, ensure_ascii=False)}\n\n"
            return
        except requests.HTTPError as exc:
            if attempt < max_attempts and _is_retryable_exception(exc):
                logger.warning("[Report] Attempt %d/%d HTTPError (%s), retrying", attempt, max_attempts, exc)
                yield f"data: {_json.dumps({'type': 'report_retrying', 'attempt': attempt + 1, 'max_attempts': max_attempts, 'message': '网络或模型暂时不稳定，正在自动重试...'}, ensure_ascii=False)}\n\n"
                await asyncio.sleep(_retry_sleep_seconds(attempt))
                continue
            logger.error("[Report] Generation failed after %d attempt(s): %s", attempt, exc, exc_info=True)
            yield f"data: {_json.dumps({'type': 'report_error', 'message': '生成失败，请稍后重试'}, ensure_ascii=False)}\n\n"
            return

    if not succeeded or not full_content:
        logger.error("[Report] No content received from LLM after %d attempt(s)", max_attempts)
        yield f"data: {_json.dumps({'type': 'report_error', 'message': '生成失败，请稍后重试'}, ensure_ascii=False)}\n\n"
        return

    logger.info("[Report] LLM response received: content_len=%d, first_200=%s", len(full_content), full_content[:200])

    try:
        report = _extract_json_object(full_content)
    except _json.JSONDecodeError as jde:
        logger.error("[Report] JSON decode error: %s", jde)
        yield f"data: {_json.dumps({'type': 'report_error', 'message': '报告内容解析失败，请重试'}, ensure_ascii=False)}\n\n"
        return

    if report is None:
        logger.warning("[Report] Failed to parse JSON from LLM response. Raw content (first 500): %s", full_content[:500])
        yield f"data: {_json.dumps({'type': 'report_error', 'message': 'LLM 未返回有效的 JSON 格式，请重试'}, ensure_ascii=False)}\n\n"
        return

    sections = report.get("sections", [])
    logger.info("[Report] Parsed %d sections", len(sections))

    report_id = str(uuid4())
    now = datetime.now(BEIJING_TZ).isoformat()
    metadata = {
        "title": f"学习报告 · {now[:10]}",
        "model": model_name,
        "created_at": now,
        "filter": {
            "mode": "time",
            "days": data.get("time_range", {}).get("days", 0),
            "start_date": data.get("time_range", {}).get("start", ""),
            "end_date": data.get("time_range", {}).get("end", ""),
            "run_ids": None,
            "total_runs": data.get("total_runs", 0),
        },
    }
    await _save_report(report_id, user_id, data, sections, metadata)

    for i, section in enumerate(sections):
        yield f"data: {_json.dumps({'type': 'report_section', 'index': i, 'section': section}, ensure_ascii=False)}\n\n"

    yield f"data: {_json.dumps({'type': 'report_done', 'message': '报告已自动保存', 'report_id': report_id}, ensure_ascii=False)}\n\n"
