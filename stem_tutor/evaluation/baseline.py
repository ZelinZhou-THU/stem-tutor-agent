from __future__ import annotations

from typing import Any

from stem_tutor.domain.models import ProblemInput
from stem_tutor.providers.base import LLMProvider
from stem_tutor.taxonomy.errors import ERROR_TAXONOMY


def _get_error_codes_with_descriptions() -> str:
    parts = []
    for code, entry in ERROR_TAXONOMY.items():
        parts.append(f"- {code}: {entry.short_desc}")
    return "\n".join(parts)


def _get_subject_name() -> str:
    try:
        from stem_tutor.subjects.context import get_subject_context
        return get_subject_context().display_name
    except Exception:
        return "学科"


def run_single_prompt_baseline(
    provider: LLMProvider,
    problem_input: ProblemInput,
    raw_student_solution: str,
    mode_name: str,
) -> dict[str, Any]:
    schema_hint = (
        '{"verification_results": [{"step_id": "S1", "label": "correct|incorrect_math|inconsistent_or_unsupported|unclear", '
        '"evidence": "string", "confidence": 0.0, "violated_principles": ["string"]}], '
        '"diagnosis_results": [{"step_id": "S1", "error_code": "string", "category": "string", '
        '"root_cause_hypothesis": "string", "supporting_evidence": "string", "confidence": 0.0}], '
        '"final_feedback": {"first_critical_step_id": "string|null", "concise_summary": "string", '
        '"likely_cause": "string|null", "review_concepts": ["string"], "next_action": "string", "caution_note": "string"}, '
        '"review_problems": [{"problem_text": "string", "related_weakness_code": "string", "rationale": "string", "difficulty_label": "easy|medium|hard"}], '
        '"uncertainty_flags": ["string"]}'
    )

    subject_name = _get_subject_name()
    error_codes = _get_error_codes_with_descriptions()

    prompt = (
        f"你是一位经验丰富的{subject_name}阅卷老师。请仔细分析学生的解题过程，"
        f"逐步完成以下任务：\n\n"
        f"1. 先自己求解本题，得到正确的参考解答\n"
        f"2. 将学生的解答划分为逻辑步骤，逐一验证是否正确\n"
        f"3. 如果有错误步骤，诊断错误的根本原因\n"
        f"4. 为学生撰写简洁的学习反馈\n"
        f"5. 生成 1-3 道相关的复习练习题\n\n"
        f"题目:\n{problem_input.problem_text}\n\n"
        f"学生解答:\n{raw_student_solution}\n\n"
        f"可选错误类型:\n{error_codes}\n\n"
        f"请返回严格的 JSON，符合以下 schema:\n{schema_hint}\n\n"
        f"注意事项:\n"
        f"- 验证步骤时，请考虑学生可能使用了与你不同的解法\n"
        f"- 诊断错误时，请从上述错误类型中选择最合适的一个\n"
        f"- 复习题应与学生的薄弱知识点相关，难度由易到难\n"
        f"- 所有输出请使用中文\n"
    )

    out = provider.invoke_structured(
        prompt,
        schema_hint,
        defaults={
            "verification_results": [],
            "diagnosis_results": [],
            "final_feedback": {
                "first_critical_step_id": None,
                "concise_summary": "",
                "likely_cause": None,
                "review_concepts": [],
                "next_action": "",
                "caution_note": "",
            },
            "review_problems": [],
            "uncertainty_flags": ["baseline_single_prompt"],
        },
    )

    info = provider.provider_info()
    return {
        "verification_results": out.get("verification_results", []),
        "diagnosis_results": out.get("diagnosis_results", []),
        "final_feedback": out.get("final_feedback", {}),
        "review_problems": out.get("review_problems", []),
        "uncertainty_flags": out.get("uncertainty_flags", []),
        "run_meta": {
            "mode": mode_name,
            "provider": info.get("provider_name", "unknown"),
            "model": info.get("model_name", "unknown"),
            "provider_events": [],
        },
        "fail_reason": None,
    }
