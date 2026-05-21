from __future__ import annotations

import base64
import json
import os
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Literal
from uuid import uuid4

import requests

from stem_tutor.evaluation.baseline import run_single_prompt_baseline
from stem_tutor.domain.models import ProblemInput
from stem_tutor.graph.workflow import run_tutor_graph
from stem_tutor.providers.factory import create_provider
from stem_tutor.settings import load_provider_settings
from stem_tutor.subjects.context import get_subject_context
from stem_tutor.subjects.detector import VALID_SUBJECTS, detect_subject
from stem_tutor.taxonomy.errors import lookup_error

RUNS_DIR = Path(__file__).resolve().parent.parent / "logs" / "runs"
RUNS_DIR.mkdir(parents=True, exist_ok=True)

REPORTS_DIR = Path(__file__).resolve().parent.parent / "logs" / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

BEIJING_TZ = timezone(timedelta(hours=8))

RUN_ATTEMPTS_BY_DEPTH = {
    "quick": 1,
    "standard": 2,
    "thorough": 3,
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
    return RUN_ATTEMPTS_BY_DEPTH.get(depth, RUN_ATTEMPTS_BY_DEPTH["standard"])


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


def _save_run_payload(run_id: str, payload: dict) -> None:
    meta = payload.setdefault("run_meta", {})
    if not meta.get("run_id"):
        meta["run_id"] = run_id
    if not meta.get("completed_at"):
        meta["completed_at"] = datetime.now(timezone.utc).isoformat()
    meta["failed"] = payload.get("status") == "failed"
    path = RUNS_DIR / f"{run_id}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)


def _save_running_placeholder(run_id: str, run_meta: dict) -> None:
    path = RUNS_DIR / f"{run_id}.json"
    if path.exists():
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
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)


def _save_intermediate_state(run_id: str, accumulated_state: dict) -> None:
    response = _shape_response(accumulated_state)
    response["status"] = "running"
    response["user_status"] = ""
    meta = response.get("run_meta", {})
    meta["failed"] = False
    response["run_meta"] = meta
    path = RUNS_DIR / f"{run_id}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(response, f, ensure_ascii=False, indent=2, default=str)


def _save_run_state(run_id: str, state: dict):
    result = _shape_response(state)
    meta = result.get("run_meta", {})
    meta["completed_at"] = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    meta["failed"] = result.get("status") == "failed"
    result["run_meta"] = meta
    path = RUNS_DIR / f"{run_id}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)


def _save_run_error(run_id: str, error_msg: str, initial_state: dict):
    result = _shape_response(initial_state)
    result["status"] = "failed"
    result["fail_reason"] = error_msg
    meta = result.get("run_meta", {})
    meta["completed_at"] = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    meta["failed"] = True
    result["run_meta"] = meta
    path = RUNS_DIR / f"{run_id}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)


def _load_run_result(run_id: str) -> dict | None:
    path = RUNS_DIR / f"{run_id}.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _get_run_status(run_id: str) -> dict:
    path = RUNS_DIR / f"{run_id}.json"
    if not path.exists():
        return {"status": "not_found", "run_id": run_id}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
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
        return {"status": "running", "run_id": run_id}
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
        entry = lookup_error(error_code)
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
        "reference_solution": state.get("reference_solution"),
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
        for d in diagnoses:
            entry = lookup_error(d.error_code)
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
    valid_escapes = set('"\\/bfnrt')
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
    depth: str = "standard",
) -> dict:
    settings = load_provider_settings()
    settings.__dict__["reasoning_model_name"] = model_name

    if depth in ("quick", "standard", "thorough"):
        os.environ["STEM_TUTOR_DEPTH"] = depth
        os.environ["STEM_TUTOR_BUDGET_ENABLED"] = "true"

    if subject_id == "auto_detect":
        subject_id = detect_subject(
            problem_text,
            base_url=settings.base_url,
            api_key=settings.api_key,
            model=settings.detection_model_name,
        )
    elif subject_id and subject_id not in VALID_SUBJECTS:
        subject_id = "calculus"

    if subject_id:
        os.environ["STEM_TUTOR_SUBJECT"] = subject_id
        get_subject_context(subject_id)

    ocr_payload = None
    if source_type == "ocr" and image_bytes:
        ocr_payload = base64.b64encode(image_bytes).decode("utf-8")

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
                state = run_single_prompt_baseline(provider, problem_input, raw_student_solution, mode_name=mode)
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
        for d in diagnoses:
            entry = lookup_error(d.error_code)
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


def cancel_run(run_id: str) -> bool:
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
    depth: str = "standard",
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

    if depth in ("quick", "standard", "thorough"):
        os.environ["STEM_TUTOR_DEPTH"] = depth
        os.environ["STEM_TUTOR_BUDGET_ENABLED"] = "true"

    if subject_id == "auto_detect":
        subject_id = detect_subject(
            problem_text,
            base_url=settings.base_url,
            api_key=settings.api_key,
            model=settings.detection_model_name,
        )
    elif subject_id and subject_id not in VALID_SUBJECTS:
        subject_id = "calculus"

    if subject_id:
        os.environ["STEM_TUTOR_SUBJECT"] = subject_id
        get_subject_context(subject_id)

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

    _save_running_placeholder(run_id, {
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
        }

        accumulated_state = dict(initial_state)

        if attempt > 1:
            yield f"data: {_json.dumps({'type': 'retrying', 'attempt': attempt, 'max_attempts': max_attempts, 'message': f'第 {attempt} 次尝试中...'}, ensure_ascii=False)}\n\n"

        try:
            if mode.startswith("baseline"):
                provider = create_provider(provider_name, settings, model_group="reasoning")
                state = run_single_prompt_baseline(provider, problem_input, raw_student_solution, mode_name=mode)
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

                        label = NODE_LABELS.get(node_name, node_name)
                        yield f"data: {_json.dumps({'type': 'node_start', 'node': node_name, 'label': label}, ensure_ascii=False)}\n\n"

                        if node_name == "parse_student_solution":
                            steps = node_output.get("normalized_steps", [])
                            step_count = len(steps)
                            yield f"data: {_json.dumps({'type': 'progress', 'node': node_name, 'detail': f'解析到 {step_count} 个解题步骤'}, ensure_ascii=False)}\n\n"

                        elif node_name == "generate_reference_solution":
                            ref_text = node_output.get("reference_solution", {}).get("reference_text", "")
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
                            _save_intermediate_state(run_id, accumulated_state)
                        except Exception:
                            pass

                response = _shape_response(accumulated_state)
        except asyncio.CancelledError:
            yield f"data: {_json.dumps({'type': 'cancelled', 'message': '分析已被用户取消'}, ensure_ascii=False)}\n\n"
            _cancel_events.pop(run_id, None)
            return
        except Exception as exc:
            last_exc = exc
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
        _save_run_payload(run_id, final_response)
        yield f"data: {_json.dumps({'type': 'safe_error', 'message': final_response.get('user_message') or GENERIC_UNAVAILABLE_MESSAGE}, ensure_ascii=False)}\n\n"
        yield f"data: {_json.dumps({'type': 'result', 'data': final_response}, ensure_ascii=False)}\n\n"
        yield f"data: {_json.dumps({'type': 'done', 'message': '分析完成'}, ensure_ascii=False)}\n\n"
        return

    if final_state is not None:
        final_state["run_meta"] = final_response.get("run_meta", {})

    _save_run_payload(run_id, final_response)
    yield f"data: {_json.dumps({'type': 'result', 'data': final_response}, ensure_ascii=False)}\n\n"
    yield f"data: {_json.dumps({'type': 'done', 'message': '分析完成'}, ensure_ascii=False)}\n\n"
    _cancel_events.pop(run_id, None)
    return


def list_runs(
    subject: str | None = None,
    status: str | None = None,
    search: str | None = None,
    page: int = 1,
    per_page: int = 20,
) -> dict:
    runs = []
    if not RUNS_DIR.exists():
        return {"runs": runs, "total": 0, "page": page, "per_page": per_page}

    for path in sorted(RUNS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError):
            continue

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
            problem_text = meta.get("problem_text", "")

        subject_id = meta.get("subject_id", "")

        if subject and subject_id != subject:
            continue
        if status:
            if status == "complete" and run_status != "complete":
                continue
            if status == "failed" and run_status not in ("unavailable", "needs_review"):
                continue
            if status == "needs_review" and run_status != "needs_review":
                continue
            if status == "unavailable" and run_status != "unavailable":
                continue
        if search and search.lower() not in problem_text.lower():
            continue

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
            "run_id": meta.get("run_id", path.stem),
            "problem_preview": (problem_text[:80] + "...") if len(problem_text) > 80 else problem_text,
            "subject": subject_id,
            "subject_display": display_name,
            "timestamp": completed or started or "",
            "status": run_status,
            "user_status": data.get("user_status", run_status),
            "user_message": data.get("user_message", ""),
            "duration_seconds": duration_seconds,
            "mode": meta.get("mode", ""),
            "model": meta.get("model", ""),
            "depth": meta.get("depth", ""),
        })

    total = len(runs)
    start = (page - 1) * per_page
    runs = runs[start:start + per_page]

    return {"runs": runs, "total": total, "page": page, "per_page": per_page}


def delete_runs(run_ids: list[str]) -> dict:
    deleted = 0
    not_found = []
    for rid in run_ids:
        run_path = RUNS_DIR / f"{rid}.json"
        chat_path = CHATS_DIR / f"{rid}.json"
        if run_path.exists():
            run_path.unlink()
            if chat_path.exists():
                chat_path.unlink()
            deleted += 1
        else:
            not_found.append(rid)
    return {"deleted": deleted, "not_found": not_found}


def cleanup_runs_before(days: int) -> dict:
    from datetime import datetime, timezone, timedelta

    if days <= 0:
        cutoff = datetime.now(timezone.utc)
    else:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    deleted = 0
    for path in list(RUNS_DIR.glob("*.json")):
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if mtime < cutoff:
            rid = path.stem
            path.unlink()
            chat_path = CHATS_DIR / f"{rid}.json"
            if chat_path.exists():
                chat_path.unlink()
            deleted += 1
    return {"deleted": deleted, "cutoff_date": cutoff.isoformat()}


def _update_step_in_run(run_id: str, step_id: str, new_result) -> None:
    path = RUNS_DIR / f"{run_id}.json"
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8"))

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

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def reverify_step(run_id: str, step_id: str) -> dict:
    run_data = _load_run_result(run_id)
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
    )

    from stem_tutor.domain.models import VerificationResult
    result = _outcome_to_verification_result(outcome, step_id)

    adj_label, adj_evidence, adj_principles = _rule_based_adjustment(target.get("normalized_text", ""))
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

    _update_step_in_run(run_id, step_id, result)

    return {
        "success": True,
        "verification_result": result.model_dump(),
        "elapsed_seconds": round(budget.elapsed(), 2),
    }


def get_stats() -> dict:
    if not RUNS_DIR.exists():
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

    for path in RUNS_DIR.glob("*.json"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError):
            continue

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


CHATS_DIR = Path(__file__).resolve().parent.parent / "logs" / "chats"
CHATS_DIR.mkdir(parents=True, exist_ok=True)


def _load_chat_history(run_id: str) -> list[dict]:
    path = CHATS_DIR / f"{run_id}.json"
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("messages", [])
    except (json.JSONDecodeError, IOError):
        return []


def _save_chat_history(run_id: str, messages: list[dict]) -> None:
    path = CHATS_DIR / f"{run_id}.json"
    data = {"run_id": run_id, "messages": messages}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def _build_chat_context(run_id: str) -> str:
    result = _load_run_result(run_id)
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
    user_message: str,
    model_name: str = "DeepSeek-V3.2",
    provider_name: str = "openai-compatible",
):
    import json as _json

    result = _load_run_result(run_id)
    if result is None:
        yield f"data: {_json.dumps({'type': 'chat_error', 'message': '分析结果不存在'}, ensure_ascii=False)}\n\n"
        return

    settings = load_provider_settings()

    context = _build_chat_context(run_id)
    system_prompt = f"""你是一位数理基础课程的辅导老师。你的职责是：
1. 基于系统分析结果回答学生的追问
2. 用清晰、通俗的语言解释数学概念
3. 如果涉及具体步骤，引用步骤编号（如"第2步"）
4. 适当给出类似题目或解题方法建议
5. 使用 $...$ 包裹行内公式，$$...$$ 包裹独立公式
6. 所有回答使用中文

{context}"""

    history = _load_chat_history(run_id)

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
        "max_tokens": 2000,
        "stream": True,
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=settings.timeout_seconds, stream=True)
        resp.raise_for_status()

        full_content = ""
        full_reasoning = ""
        for line in resp.iter_lines():
            line = line.decode("utf-8")
            if not line.startswith("data:"):
                continue
            data_str = line[5:].strip()
            if data_str == "[DONE]":
                break
            try:
                chunk_data = json.loads(data_str)
                delta = chunk_data.get("choices", [{}])[0].get("delta", {})
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

        assistant_ts = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
        history.append({"role": "assistant", "content": full_content, "ts": assistant_ts, "model": model_name})
        _save_chat_history(run_id, history)

        yield f"data: {_json.dumps({'type': 'chat_done', 'message': '回复完成'}, ensure_ascii=False)}\n\n"

    except Exception as exc:
        yield f"data: {_json.dumps({'type': 'chat_error', 'message': str(exc)}, ensure_ascii=False)}\n\n"


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


def get_report_run_list() -> list[dict]:
    if not RUNS_DIR.exists():
        return []

    runs = []
    for path in sorted(RUNS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError):
            continue

        meta = data.get("run_meta", {})
        run_id = meta.get("run_id", path.stem)
        subject_id = meta.get("subject_id", "")
        started = meta.get("started_at", "")
        completed = meta.get("completed_at", "")
        timestamp = completed or started or ""

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
            problem_text = meta.get("problem_text", "")

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

    return runs


def get_report_data(
    days: int = 30,
    start_date: str | None = None,
    end_date: str | None = None,
    run_ids: list[str] | None = None,
) -> dict:
    from datetime import timedelta

    if not RUNS_DIR.exists():
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
    for path in RUNS_DIR.glob("*.json"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError):
            continue

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

            entry = lookup_error(error_code)
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
        subj_runs = [d for d in runs_data if subject_display_map.get(d.get("run_meta", {}).get("subject_id", ""), "") == subj]
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
    }


def _save_report(report_id: str, report_data: dict, sections: list, metadata: dict) -> None:
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
    path = REPORTS_DIR / f"{report_id}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    logger.info("[Report] Saved report %s to %s", report_id, path)


def _load_report(report_id: str) -> dict | None:
    path = REPORTS_DIR / f"{report_id}.json"
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def list_reports(page: int = 1, per_page: int = 20) -> dict:
    reports = []
    if not REPORTS_DIR.exists():
        return {"reports": reports, "total": 0, "page": page, "per_page": per_page}

    for path in sorted(REPORTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError):
            continue

        reports.append({
            "report_id": data.get("report_id", path.stem),
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


def delete_reports(report_ids: list[str]) -> dict:
    deleted = 0
    not_found = []
    for rid in report_ids:
        path = REPORTS_DIR / f"{rid}.json"
        if path.exists():
            path.unlink()
            deleted += 1
        else:
            not_found.append(rid)
    return {"deleted": deleted, "not_found": not_found}


async def report_stream(data: dict, model_name: str = "qwen/qwen3.6-plus"):
    import asyncio
    import json as _json
    import logging

    from stem_tutor.prompts.templates import report_prompt

    logger = logging.getLogger(__name__)

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
        "max_tokens": 8000,
        "stream": False,
    }

    logger.info("[Report] LLM request: model=%s, prompt_len=%d, max_tokens=8000", model_name, len(prompt))

    try:
        import functools
        resp = await asyncio.to_thread(
            functools.partial(requests.post, url, json=payload, headers=headers, timeout=300)
        )
        resp.raise_for_status()
        result = resp.json()
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")

        logger.info("[Report] LLM response received: content_len=%d, first_200=%s", len(content), content[:200])

        report = _extract_json_object(content)
        if report is None:
            logger.warning("[Report] Failed to parse JSON from LLM response. Raw content (first 500): %s", content[:500])
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
        _save_report(report_id, data, sections, metadata)

        for i, section in enumerate(sections):
            yield f"data: {_json.dumps({'type': 'report_section', 'index': i, 'section': section}, ensure_ascii=False)}\n\n"

        yield f"data: {_json.dumps({'type': 'report_done', 'message': '报告已自动保存', 'report_id': report_id}, ensure_ascii=False)}\n\n"

    except _json.JSONDecodeError as jde:
        logger.error("[Report] JSON decode error: %s", jde)
        yield f"data: {_json.dumps({'type': 'report_error', 'message': '报告内容解析失败，请重试'}, ensure_ascii=False)}\n\n"
    except Exception as exc:
        logger.error("[Report] Generation failed: %s", exc, exc_info=True)
        yield f"data: {_json.dumps({'type': 'report_error', 'message': f'生成失败：{exc}'}, ensure_ascii=False)}\n\n"
