from __future__ import annotations

import difflib
import json
import logging
import re

import requests

VALID_SUBJECTS = {
    "calculus",
    "linear_algebra",
    "mechanics",
    "relativity",
    "optics",
    "quantum",
    "electromagnetism",
    "thermodynamics",
}

_SUBJECT_LIST = ", ".join(sorted(VALID_SUBJECTS))

_DETECTION_PROMPT = (
    "从以下列表中选出最匹配的学科ID，只返回学科ID本身，不要包含任何其他文字。\n"
    f"可选学科ID: {_SUBJECT_LIST}\n"
    "题目: {problem_text}"
)

_DEFAULT_SUBJECT = "calculus"
_DETECTION_TIMEOUT = 30


def detect_subject(problem_text: str, base_url: str, api_key: str, model: str = "GLM-4-FlashX") -> str:
    """Detect subject from problem text using LLM. Returns subject_id or default on failure."""
    prompt = _DETECTION_PROMPT.format(problem_text=problem_text[:500])

    try:
        resp = requests.post(
            f"{base_url}/chat/completions",
            json={
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a subject classifier for math/physics problems. Return only the subject ID.",
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.0,
                "max_tokens": 20,
            },
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=_DETECTION_TIMEOUT,
        )

        if not resp.ok:
            logging.warning("[SubjectDetect] HTTP %s: %s", resp.status_code, resp.text[:200])
            return _DEFAULT_SUBJECT

        content = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        subject_id = _extract_subject_id(content)

        if subject_id:
            logging.info("[SubjectDetect] Detected: %s (from text: %s)", subject_id, content)
            return subject_id

        logging.warning("[SubjectDetect] Unrecognized response: %s", content)
        return _DEFAULT_SUBJECT

    except Exception as exc:
        logging.warning("[SubjectDetect] Detection failed, falling back to default: %s", exc)
        return _DEFAULT_SUBJECT


def _extract_subject_id(text: str) -> str | None:
    """Extract a valid subject ID from LLM response text."""
    cleaned = text.strip().strip("`").strip().strip('"').strip()
    cleaned = cleaned.lower()
    cleaned = re.sub(r"[^a-z_]", "", cleaned)

    if cleaned in VALID_SUBJECTS:
        return cleaned

    for subject in VALID_SUBJECTS:
        if subject in cleaned or cleaned in subject:
            return subject

    matches = difflib.get_close_matches(cleaned, VALID_SUBJECTS, n=1, cutoff=0.7)
    if matches:
        logging.info("[SubjectDetect] Fuzzy matched '%s' → '%s'", cleaned, matches[0])
        return matches[0]

    return None
