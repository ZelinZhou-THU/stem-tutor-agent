from __future__ import annotations

import json
import random
import sys
import time
from typing import Any

import requests

from stem_tutor.domain.models import (
    DiagnosisPayload,
    FeedbackPayload,
    ReferenceSolutionPayload,
    ReviewProblemsPayload,
    VerificationLabel,
    VerificationPayload,
)
from stem_tutor.providers.base import LLMProvider
from stem_tutor.settings import ProviderSettings
from stem_tutor.subjects.context import get_subject_context


def _log(msg: str) -> None:
    print(f"[LLM-DEBUG] {msg}", flush=True, file=sys.stderr)


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


def _fix_json_control_chars(text: str) -> str:
    in_string = False
    escape_next = False
    result = []
    for c in text:
        if escape_next:
            escape_next = False
            result.append(c)
            continue
        if c == "\\":
            escape_next = True
            result.append(c)
            continue
        if c == '"':
            in_string = not in_string
            result.append(c)
            continue
        if in_string:
            if c == "\n":
                result.append("\\n")
            elif c == "\r":
                result.append("\\r")
            elif c == "\t":
                result.append("\\t")
            else:
                result.append(c)
        else:
            result.append(c)
    return "".join(result)


def _is_retryable_http_status(status: int | None) -> bool:
    if status is None:
        return True
    return status == 429 or status >= 500


def _retry_backoff_seconds(attempt_idx: int) -> float:
    base = min(0.6 * (2 ** attempt_idx), 3.0)
    jitter = random.uniform(0.0, 0.2)
    return base + jitter


class OpenAICompatibleProvider(LLMProvider):
    """Provider for OpenAI-compatible chat completions endpoints."""

    def __init__(self, settings: ProviderSettings, model_name: str, model_group: str = "reasoning"):
        super().__init__()
        if not settings.api_key:
            raise ValueError("Missing API key for real provider")
        if not settings.base_url:
            raise ValueError("Missing base URL for real provider")
        if not model_name:
            raise ValueError("Missing model name for real provider")

        self.settings = settings
        self.model_name = model_name
        self.model_group = model_group
        self.base_url = settings.base_url.rstrip("/")

    def health_check(self) -> tuple[bool, str]:
        url = f"{self.base_url}/models"
        headers = {"Authorization": f"Bearer {self.settings.api_key}"}
        try:
            resp = requests.get(url, headers=headers, timeout=self.settings.timeout_seconds)
            return (resp.ok, f"status={resp.status_code}")
        except Exception as exc:
            return (False, repr(exc))

    def provider_info(self) -> dict[str, str]:
        return {
            "provider_name": "openai-compatible",
            "model_name": self.model_name,
        }

    def _get_system_prompt(self) -> str:
        try:
            ctx = get_subject_context()
            return ctx.prompts["system_role"].replace("{subject_name}", ctx.display_name)
        except Exception:
            return "你是一个精确的微积分辅导 JSON API。所有输出请使用中文（简体中文）。数学表达式请用 $...$ 包裹行内公式，用 $$...$$ 包裹独立公式。"

    def _chat(self, prompt: str, schema_hint: str) -> dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
        }
        user_content = (
            f"{prompt}\n\n"
            "请只返回有效的 JSON，不要使用 markdown 代码块包裹。"
            f"必须符合以下 schema: {schema_hint}"
        )
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": self._get_system_prompt()},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.2,
            "max_tokens": 2000,
        }

        _log(f"_chat START model={self.model_name} timeout={self.settings.timeout_seconds}s prompt_len={len(prompt)}")
        
        resp = None
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=self.settings.timeout_seconds)
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"].get("content")
            if content is None:
                _log(f"_chat EMPTY_RESPONSE model={self.model_name}")
                raise ValueError("Model returned empty content (content field is null)")
            _log(f"_chat OK response_len={len(content)} first_200={content[:200]}")
            return self._parse_json_object(content)
        except requests.Timeout as exc:
            _log(f"_chat TIMEOUT after {self.settings.timeout_seconds}s model={self.model_name}")
            raise
        except requests.HTTPError as exc:
            body = resp.text[:500] if resp is not None else "N/A"
            status = resp.status_code if resp is not None else "N/A"
            _log(f"_chat HTTP_ERROR status={status} body={body}")
            raise

    @staticmethod
    def _parse_json_object(text: str) -> dict[str, Any]:
        raw = text.strip()
        if raw.startswith("```"):
            first_nl = raw.find("\n")
            if first_nl >= 0:
                raw = raw[first_nl + 1:]
            else:
                raw = raw[3:]
            closing = raw.rfind("```")
            if closing > 0:
                raw = raw[:closing]
            raw = raw.strip()

        start = raw.find("{")
        if start < 0:
            raise ValueError("No JSON object found in model output")

        depth = 0
        in_string = False
        escape_next = False
        for i in range(start, len(raw)):
            c = raw[i]
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
                    candidate = raw[start : i + 1]
                    try:
                        parsed = json.loads(candidate)
                        if isinstance(parsed, dict):
                            return parsed
                    except json.JSONDecodeError:
                        pass
                    fixed1 = _fix_json_control_chars(candidate)
                    try:
                        parsed = json.loads(fixed1)
                        if isinstance(parsed, dict):
                            return parsed
                    except json.JSONDecodeError:
                        pass
                    fixed2 = _fix_json_escapes(fixed1)
                    try:
                        parsed = json.loads(fixed2)
                        if isinstance(parsed, dict):
                            return parsed
                    except json.JSONDecodeError:
                        pass
                    raise ValueError(f"Could not parse JSON: {candidate[:100]}")
        raise ValueError("No complete JSON object found in model output")

    def invoke_structured(self, prompt: str, schema_hint: str, defaults: dict[str, Any]) -> dict[str, Any]:
        attempts = self.settings.max_retries + 1
        last_error = ""
        last_error_type = "unknown"

        _log(f"invoke_structured START model={self.model_name} timeout={self.settings.timeout_seconds}s retries={attempts-1}")
        
        for attempt in range(attempts):
            _log(f"invoke_structured attempt {attempt+1}/{attempts}")
            retryable = False
            try:
                out = self._chat(prompt, schema_hint)
                for k, v in defaults.items():
                    out.setdefault(k, v)
                self.set_last_call_meta(error_type=None, retries=attempt, used_fallback=False)
                _log(f"invoke_structured OK attempt={attempt+1}")
                return out
            except requests.Timeout as exc:
                last_error = repr(exc)
                error_type = "timeout"
                retryable = True
                _log(f"invoke_structured TIMEOUT attempt={attempt+1}")
            except requests.HTTPError as exc:
                last_error = repr(exc)
                status = getattr(exc.response, "status_code", None)
                error_type = f"http_{status}" if status is not None else "http"
                retryable = _is_retryable_http_status(status)
                _log(f"invoke_structured HTTP_ERROR attempt={attempt+1} status={status} retryable={retryable}")
            except ValueError as exc:
                last_error = repr(exc)
                error_type = "json_parse"
                retryable = True
                _log(f"invoke_structured JSON_PARSE attempt={attempt+1}")
            except Exception as exc:
                last_error = repr(exc)
                error_type = "unknown"
                retryable = False
                _log(f"invoke_structured UNKNOWN attempt={attempt+1}: {last_error}")

            last_error_type = error_type

            if attempt == attempts - 1 or not retryable:
                self.set_last_call_meta(error_type=error_type, retries=attempt, used_fallback=True)
                fallback = dict(defaults)
                fallback.setdefault("provider_error", last_error)
                _log(f"invoke_structured FALLBACK error_type={error_type} last_error={last_error[:200]}")
                return fallback

            delay = _retry_backoff_seconds(attempt)
            _log(f"invoke_structured RETRY wait={delay:.2f}s")
            time.sleep(delay)

        self.set_last_call_meta(error_type=last_error_type, retries=attempts, used_fallback=True)
        return dict(defaults)

    def parse_steps(self, raw_solution: str) -> list[dict[str, str]] | None:
        schema = '{"steps": [{"text": "string", "description": "string"}]}'
        prompt = (
            "你是一位数学解题分析专家。请将学生的解题过程划分为逻辑完整的步骤。\n\n"
            "【核心原则】\n"
            "1. 每个步骤必须包含一个完整的数学推导或逻辑论证，不能是碎片化的中间形式\n"
            "2. 连续的等式推导（A = B = C = D）必须合并为一个步骤，不要按等号拆分\n"
            "3. 如果某行以 '=' 开头，说明它是上一行的延续，必须合并到上一步\n"
            "4. 如果某一步只有结果（如 '= 16/15'）而没有新的数学操作，必须合并到上一步\n\n"
            "【具体场景处理】\n"
            "- 换元法：从原式到设变量到代换到简化积分，应作为一个完整步骤\n"
            "- Beta/Gamma函数：从识别函数形式到代入公式到展开计算到得出结果，可作为一个或两个步骤\n"
            "- 证明题：按'假设到推导到结论'的逻辑链划分，每个逻辑阶段为一个步骤\n"
            "- 计算题：按'化简到代入到计算到结果'的阶段划分\n\n"
            "【禁止行为】\n"
            "- 禁止将连续的等式链拆成多个步骤\n"
            "- 禁止将只有结果而无推导的行作为独立步骤\n"
            "- 禁止将换元法的不同阶段拆成碎片\n\n"
            f"学生解题过程：\n{raw_solution}\n\n"
            "text 字段：该步骤的原始文本（保持原样，包含所有 LaTeX 符号）\n"
            "description 字段：用一句话描述这一步做了什么\n"
        )
        raw = self.invoke_structured(prompt, schema, defaults={"steps": []})
        steps = raw.get("steps")
        if steps and isinstance(steps, list) and all(isinstance(s, dict) and "text" in s for s in steps):
            return steps
        return None

    def ocr_to_text(self, ocr_payload: str) -> dict[str, Any]:
        schema = '{"text": "string", "quality_score": 0.0, "warnings": ["string"], "formula_format": "string"}'
        prompt = (
            "You are an OCR transcriber. Convert the input image description or encoded payload into plain text "
            "with math formulas in latex-like text. Preserve line-by-line step structure.\n"
            f"OCR payload: {ocr_payload}"
        )
        raw = self.invoke_structured(
            prompt,
            schema,
            defaults={
                "text": "",
                "quality_score": 0.5,
                "warnings": ["ocr_fallback_used"],
                "formula_format": "latex_like",
            },
        )
        return {
            "text": str(raw.get("text", "")),
            "quality_score": float(raw.get("quality_score", 0.5)),
            "warnings": list(raw.get("warnings", [])),
            "formula_format": str(raw.get("formula_format", "latex_like")),
        }

    def generate_reference_solution(self, problem_text: str) -> dict[str, Any]:
        prompt = f"题目: {problem_text}"
        schema = '{"reference_text": "string", "key_assertions": ["string"]}'
        user_content = (
            f"{prompt}\n\n"
            "请只返回有效的 JSON，不要使用 markdown 代码块包裹。"
            f"必须符合以下 schema: {schema}\n"
            "请提供完整的分步解答。所有输出请使用中文。"
            "数学表达式请用 $...$ 包裹行内公式，用 $$...$$ 包裹独立公式。"
        )
        
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": self._get_system_prompt()},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.2,
            "max_tokens": 4000,
        }

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
        }

        _log(f"generate_reference_solution START model={self.model_name} timeout={self.settings.timeout_seconds}s")
        resp = None
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=self.settings.timeout_seconds)
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            _log(f"generate_reference_solution OK response_len={len(content)} first_300={content[:300]}")
        except requests.Timeout:
            _log(f"generate_reference_solution TIMEOUT after {self.settings.timeout_seconds}s")
            raise
        except requests.HTTPError:
            body = resp.text[:500] if resp is not None else "N/A"
            status = resp.status_code if resp is not None else "N/A"
            _log(f"generate_reference_solution HTTP_ERROR status={status} body={body}")
            raise
        
        raw = self._parse_json_object(content)
        _log(f"generate_reference_solution parsed_keys={list(raw.keys()) if isinstance(raw, dict) else 'not dict'}")
        
        defaults = {
            "reference_text": f"Reference solution unavailable for: {problem_text}",
            "key_assertions": [],
        }
        
        for k, v in defaults.items():
            raw.setdefault(k, v)
            
        return self.validate_or_fallback(
            raw,
            ReferenceSolutionPayload,
            ReferenceSolutionPayload(
                reference_text=f"Reference solution unavailable for: {problem_text}",
                key_assertions=[],
            ),
        )

    def verify_step(self, prompt: str) -> dict[str, Any]:
        schema = (
            '{"label": "correct|incorrect_math|inconsistent_or_unsupported|unclear", '
            '"evidence": "string（请用中文描述判断依据）", "confidence": 0.0, "violated_principles": ["string"]}'
        )
        _log(f"verify_step START prompt_len={len(prompt)}")
        raw = self.invoke_structured(
            prompt,
            schema,
            defaults={
                "label": VerificationLabel.UNCLEAR.value,
                "evidence": "LLM 返回格式无效。",
                "confidence": 0.5,
                "violated_principles": [],
            },
        )
        meta = self.get_last_call_meta()
        _log(f"verify_step RESULT raw_keys={list(raw.keys()) if isinstance(raw, dict) else 'not dict'} meta={meta}")
        return self.validate_or_fallback(
            raw,
            VerificationPayload,
            VerificationPayload(
                label=VerificationLabel.UNCLEAR,
                evidence="LLM 返回格式无效。",
                confidence=0.2,
                violated_principles=["schema_validation"],
            ),
        )

    def diagnose_error(self, prompt: str) -> dict[str, Any]:
        schema = (
            '{"error_code": "string", "root_cause_hypothesis": "string（请用中文描述）", '
            '"supporting_evidence": "string（请用中文描述）", "confidence": 0.0}'
        )
        _log(f"diagnose_error START prompt_len={len(prompt)}")
        raw = self.invoke_structured(
            prompt,
            schema,
            defaults={
                "error_code": "NOTATION_UNCLEAR",
                "root_cause_hypothesis": "无法确定具体错误原因。",
                "supporting_evidence": "回退到默认诊断。",
                "confidence": 0.5,
            },
        )
        _log(f"diagnose_error RESULT raw_keys={list(raw.keys()) if isinstance(raw, dict) else 'not dict'}")
        return self.validate_or_fallback(
            raw,
            DiagnosisPayload,
            DiagnosisPayload(
                error_code="NOTATION_UNCLEAR",
                root_cause_hypothesis="无法确定具体错误原因。",
                supporting_evidence="回退到默认诊断。",
                confidence=0.2,
            ),
        )

    def generate_feedback(self, prompt: str) -> dict[str, Any]:
        schema = '{"concise_summary": "string", "next_action": "string", "caution_note": "string"}'
        _log(f"generate_feedback START prompt_len={len(prompt)}")
        raw = self.invoke_structured(
            prompt,
            schema,
            defaults={
                "concise_summary": "反馈生成异常，请检查首个标记步骤。",
                "next_action": "重写首个不正确的步骤，并用一条规则证明每次变换。",
                "caution_note": "因格式验证失败使用了回退反馈。",
            },
        )
        _log(f"generate_feedback RESULT raw_keys={list(raw.keys()) if isinstance(raw, dict) else 'not dict'}")
        return self.validate_or_fallback(
            raw,
            FeedbackPayload,
            FeedbackPayload(
                concise_summary="反馈生成异常，请检查首个标记步骤。",
                next_action="重写首个不正确的步骤，并用一条规则证明每次变换。",
                caution_note="因格式验证失败使用了回退反馈。",
            ),
        )

    def generate_review_problems(self, prompt: str) -> dict[str, Any]:
        schema = (
            '{"problems": [{"problem_text": "string（请用中文出题）", "related_weakness_code": "string", '
            '"rationale": "string（请用中文说明）", "difficulty_label": "easy|medium|hard"}]}'
        )
        _log(f"generate_review_problems START prompt_len={len(prompt)}")
        raw = self.invoke_structured(
            prompt,
            schema,
            defaults={
                "problems": [],
            },
        )
        _log(f"generate_review_problems RESULT raw_keys={list(raw.keys()) if isinstance(raw, dict) else 'not dict'}")
        return self.validate_or_fallback(
            raw,
            ReviewProblemsPayload,
            ReviewProblemsPayload(problems=[]),
        )
