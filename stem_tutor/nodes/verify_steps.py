from __future__ import annotations

import logging
import os
import re as _re
import time as _time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from stem_tutor.domain.models import VerificationLabel, VerificationPayload, VerificationResult
from stem_tutor.graph.observability import record_provider_call
from stem_tutor.graph.state import TutorGraphState
from stem_tutor.prompts.templates import final_answer_verification_prompt, verification_prompt
from stem_tutor.providers.base import LLMProvider
from stem_tutor.settings import is_sympy_enabled
from stem_tutor.subjects.context import get_subject_context

LOW_CONF_THRESHOLD = 0.6
MAX_VERIFY_WORKERS = 5
SEQUENTIAL_THRESHOLD = 3
MIN_PER_STEP_SECONDS = 15


def _is_tool_calling_enabled() -> bool:
    try:
        from stem_tutor.settings import is_tool_calling_enabled as _check
        return _check()
    except Exception:
        return False


def _is_budget_enabled(state: dict | None = None) -> bool:
    if state is not None and "budget_enabled" in state:
        return bool(state["budget_enabled"])
    val = os.environ.get("STEM_TUTOR_BUDGET_ENABLED", "").strip().lower()
    return val in {"1", "true", "yes", "on"}


def _verify_step_via_agent(
    problem_text: str,
    reference_solution: str,
    step_text: str,
    step_id: str,
    total_steps: int,
    full_solution: str,
    prev_text: str,
    next_text: str,
    assertions: list[str],
    final_answer_status: str,
    reference_answer_hint: str = "",
    computation_hints: str = "",
    model_name: str | None = None,
) -> tuple[dict, list[dict]]:
    from stem_tutor.graph.agent_subgraph import AgentSubgraph, parse_json_from_text
    from stem_tutor.settings import is_dual_model_enabled, load_provider_settings
    from stem_tutor.subjects.context import get_subject_context

    settings = load_provider_settings()
    ctx = get_subject_context()
    display_name = ctx.display_name

    system_prompt = (
        ctx.prompts.get("verification_role", f"你是一位{display_name}阅卷老师。请判断学生的解题步骤是否正确。返回 JSON 格式。")
        .replace("{subject_name}", display_name)
        + "\n\n你可以使用 execute_python 工具验证学生的计算是否正确。"
        "请编写 Python 代码（支持 sympy/numpy/scipy）执行独立验证，用 print() 输出验证结论。\n\n"
        "【严禁操作 - blacklist=v1】\n"
        "- 禁止使用 sp.integrate() 或 sympy.integrate()，会导致超时。用 scipy.integrate.quad() 做数值验证。\n"
        "- 代码总长度控制在 600 字符以内。\n\n"
        "【验证规范 - efficiency=v2】\n"
        "- 选择最短、最直接的验证路径。\n"
        "- 优先使用数值方法（scipy.integrate.quad, numpy）而非符号积分。\n"
        "- 避免对简单步骤（如基本代数运算）发起工具调用，优先基于数学推理判断。\n"
        "- 将验证逻辑写在同一次 execute_python 调用中。\n\n"
        "【输出规范 - tool_prompt_policy=v1】\n"
        "- print() 输出优先使用 ASCII 字符，避免 Unicode 特殊字符（如 ∫、√、₀、¹ 等）。\n"
        "- 在代码末尾添加固定输出锚点：\n"
        "  CHECK_PASS=<true/false，表示验证是否通过>\n"
        "  KEY_RESULT=<关键验证结果摘要>\n"
        "- 仅使用允许库：sympy、numpy、scipy、math、fractions、json，禁止其他第三方库。\n"
        "- 若执行失败，不要重试，直接基于数学推理给出判断。\n\n"
        "最终请只返回有效的 JSON，不要使用 markdown 代码块包裹。格式如下：\n"
        '{"label": "correct|incorrect_math|inconsistent_or_unsupported|unclear", '
        '"evidence": "判断依据（中文）", "confidence": 0.0, "violated_principles": ["string"]}'
    )

    verification_extra = ctx.prompts.get("verification_extra", "")
    if verification_extra:
        system_prompt += f"\n\n【验证要求】\n{verification_extra}"

    resolved_model = model_name or settings.reasoning_model_name
    tool_model = settings.fast_model_name if is_dual_model_enabled() else None
    agent = AgentSubgraph(
        api_key=settings.api_key,
        base_url=settings.base_url,
        model_name=resolved_model,
        system_prompt=system_prompt,
        max_tokens=2000,
        tool_model_name=tool_model,
    )

    user_parts = [
        f"题目: {problem_text}\n",
        f"参考解答: {reference_solution}\n",
    ]
    if full_solution:
        user_parts.append(f"\n【学生完整解题过程】\n{full_solution}\n")
    user_parts.append(f"\n【当前验证步骤】第 {step_id} 步（共 {total_steps} 步）\n")
    user_parts.append(f"当前步骤内容: {step_text}\n")
    if prev_text:
        user_parts.append(f"上一步: {prev_text}\n")
    else:
        user_parts.append("上一步: （第一步）\n")
    if next_text:
        user_parts.append(f"下一步: {next_text}\n")
    else:
        user_parts.append("下一步: （最后一步）\n")
    if assertions:
        user_parts.append(f"\n参考解答关键断言: {', '.join(assertions)}\n")
    if final_answer_status:
        user_parts.append(f"\n最终答案验证结果: {final_answer_status}\n")
    if reference_answer_hint:
        user_parts.append(f"\n【参考答案校验】\n{reference_answer_hint}\n")
    if computation_hints:
        user_parts.append(f"\n【预计算结果】\n{computation_hints}\n")
    user_parts.append("\n请判断当前步骤是否正确，最终只返回 JSON。")

    max_retries = 3
    last_exc = None
    for attempt in range(max_retries):
        try:
            agent_result = agent.invoke("".join(user_parts), max_iterations=2)
            break
        except Exception as e:
            last_exc = e
            if attempt < max_retries - 1:
                wait = 5 * (attempt + 1)
                logging.warning(
                    f"[verify_steps] Agent invoke attempt {attempt + 1}/{max_retries} "
                    f"failed: {e}, retrying in {wait}s"
                )
                _time.sleep(wait)
            else:
                raise last_exc
    messages = agent_result.messages
    last_ai = agent.get_last_ai_message(messages)
    if last_ai is None:
        raise ValueError("Agent produced no AI message")

    raw = parse_json_from_text(last_ai.content)
    if "label" not in raw:
        raise ValueError(f"No 'label' in agent response: {list(raw.keys())}")
    _VALID_LABELS = {"correct", "incorrect_math", "inconsistent_or_unsupported", "unclear"}
    if raw.get("label") not in _VALID_LABELS:
        raw["label"] = "unclear"
        raw["evidence"] = raw.get("evidence", "") + " [LLM 返回了非标准标签，已降级为 unclear]"
    return raw, agent_result.tool_calls


def _get_rule_adjustments() -> list[dict[str, Any]]:
    try:
        ctx = get_subject_context()
        return ctx.rule_adjustments
    except Exception:
        return [
            {
                "conditions": [{"type": "contains", "value": "therefore"}, {"type": "not_contains", "value": "="}],
                "label": "inconsistent_or_unsupported",
                "evidence": "Step uses an inference marker without a concrete equivalence.",
                "violated_principles": ["insufficient_justification"],
            },
            {
                "conditions": [{"type": "contains", "value": "u ="}, {"type": "not_contains", "value": "du"}],
                "label": "incorrect_math",
                "evidence": "Substitution appears without differential mapping.",
                "violated_principles": ["substitution_mapping"],
            },
        ]


def _check_conditions(step_text: str, conditions: list[dict[str, str]]) -> bool:
    normalized = step_text.lower()
    for cond in conditions:
        cond_type = cond.get("type", "")
        value = cond.get("value", "").lower()
        if cond_type == "contains" and value not in normalized:
            return False
        if cond_type == "not_contains" and value in normalized:
            return False
    return True


def _rule_based_adjustment(step_text: str) -> tuple[VerificationLabel | None, str, list[str]]:
    rule_adjustments = _get_rule_adjustments()
    for rule in rule_adjustments:
        if _check_conditions(step_text, rule["conditions"]):
            label_str = rule.get("label", "")
            label_map = {
                "correct": VerificationLabel.CORRECT,
                "incorrect_math": VerificationLabel.INCORRECT_MATH,
                "inconsistent_or_unsupported": VerificationLabel.INCONSISTENT_OR_UNSUPPORTED,
                "unclear": VerificationLabel.UNCLEAR,
            }
            label = label_map.get(label_str)
            if label is not None:
                return (label, rule.get("evidence", ""), rule.get("violated_principles", []))
    return (None, "", [])


def _is_final_answer_step(step) -> bool:
    return step.normalized_text.startswith("最终答案:")


def _deterministic_pre_check(
    step_text: str,
    prev_text: str,
    reference_answer_hint: str,
) -> bool | None:
    stripped = step_text.strip()
    if not stripped or len(stripped) < 3:
        return None

    eq_match = _re.match(
        r'^[\s]*([+-]?\d+(?:\.\d+)?)\s*([+\-*/\u00d7\u00f7])\s*([+-]?\d+(?:\.\d+)?)\s*=\s*([+-]?\d+(?:\.\d+)?)[\s]*$',
        stripped,
    )
    if eq_match:
        try:
            a, op, b, expected = float(eq_match.group(1)), eq_match.group(2), float(eq_match.group(3)), float(eq_match.group(4))
            ops = {
                "+": lambda x, y: x + y,
                "-": lambda x, y: x - y,
                "*": lambda x, y: x * y,
                "\u00d7": lambda x, y: x * y,
                "/": lambda x, y: x / y if y != 0 else None,
                "\u00f7": lambda x, y: x / y if y != 0 else None,
            }
            fn = ops.get(op)
            if fn is not None:
                result = fn(a, b)
                if result is not None:
                    return abs(result - expected) < 1e-9
        except (ValueError, ZeroDivisionError):
            pass

    if reference_answer_hint:
        ref_nums = _extract_numeric_values(reference_answer_hint)
        step_nums = _extract_numeric_values(stripped)
        if ref_nums and step_nums:
            for sn in step_nums:
                if any(abs(sn - rn) < 1e-6 for rn in ref_nums):
                    return True

    return None


def _extract_reference_answer_hint(reference_text: str, assertions: list[str]) -> str:
    boxed = _re.search(r'\\boxed\{([^}]+)\}', reference_text)
    if boxed:
        answer = boxed.group(1).strip()
        return (
            f"参考解答的最终计算结果为: {answer}\n"
            "请在验证时特别关注：如果学生步骤中出现的最终数值或表达式结果与参考答案不一致，"
            "则解题过程中至少有一个步骤存在错误（如丢失系数、计算错误等），应判定为 incorrect_math。"
        )
    for assertion in reversed(assertions):
        nums = _extract_numeric_values(assertion)
        if nums:
            return (
                f"参考解答的最终计算结果约为: {nums[-1]:.6g}\n"
                "请在验证时特别关注：如果学生步骤中出现的最终数值结果与参考答案不一致，"
                "则解题过程中至少有一个步骤存在错误，应判定为 incorrect_math。"
            )
    return ""


def _extract_numeric_values(text: str) -> list[float]:
    values: list[float] = []
    for m in _re.finditer(r'\\(?:d)?frac\{([^}]+)\}\{([^}]+)\}', text):
        try:
            num = _eval_simple_number(m.group(1))
            den = _eval_simple_number(m.group(2))
            if den != 0:
                values.append(num / den)
        except (ValueError, ZeroDivisionError):
            pass
    for m in _re.finditer(r'(?<![/\w])(\d+)\s*/\s*(\d+)(?![/\w])', text):
        num, den = int(m.group(1)), int(m.group(2))
        if den != 0:
            values.append(num / den)
    return values


def _eval_simple_number(s: str) -> float:
    s = s.strip()
    if _re.match(r'^-?\d+$', s):
        return float(s)
    return float(s)


def _verify_final_answer(provider: LLMProvider, problem_text: str, answer_text: str) -> dict | None:
    prompt = final_answer_verification_prompt(problem_text, answer_text)
    schema_hint = '{"is_correct": true, "correct_answer": "string", "explanation": "string"}'
    defaults = {"is_correct": None, "correct_answer": "", "explanation": ""}
    raw = provider.invoke_structured(prompt, schema_hint, defaults)
    if raw.get("is_correct") is not None:
        return raw
    return None


def _verify_payload_with_retry(provider: LLMProvider, prompt: str, retries: int = 1) -> tuple[dict, bool]:
    for attempt in range(retries + 1):
        raw = provider.verify_step(prompt)
        if isinstance(raw, dict) and raw.get("label") in {
            "correct",
            "incorrect_math",
            "inconsistent_or_unsupported",
            "unclear",
        }:
            return raw, False

        if attempt >= retries:
            break

    return {
        "label": "unclear",
        "evidence": "LLM 返回格式无效。",
        "confidence": 0.2,
        "violated_principles": ["schema_validation"],
    }, True


def _outcome_to_verification_result(outcome, step_id: str) -> VerificationResult:
    from stem_tutor.graph.strategy import StrategyOutcome
    if outcome.data is None:
        return VerificationResult(
            step_id=step_id,
            label=VerificationLabel.UNCLEAR,
            evidence="All verification strategies failed",
            confidence=0.25,
            violated_principles=["all_strategies_failed"],
        )

    data = outcome.data
    label_str = data.get("label", "unclear") if isinstance(data, dict) else "unclear"
    label_map = {
        "correct": VerificationLabel.CORRECT,
        "incorrect_math": VerificationLabel.INCORRECT_MATH,
        "inconsistent_or_unsupported": VerificationLabel.INCONSISTENT_OR_UNSUPPORTED,
        "unclear": VerificationLabel.UNCLEAR,
    }
    label = label_map.get(label_str, VerificationLabel.UNCLEAR)

    sympy_verified = outcome.metadata.get("sympy_verified", False)
    sympy_equivalent = outcome.metadata.get("sympy_equivalent")

    return VerificationResult(
        step_id=step_id,
        label=label,
        evidence=data.get("evidence", "") if isinstance(data, dict) else "",
        confidence=data.get("confidence", outcome.confidence) if isinstance(data, dict) else outcome.confidence,
        violated_principles=data.get("violated_principles", []) if isinstance(data, dict) else [],
        sympy_verified=sympy_verified,
        sympy_equivalent=sympy_equivalent,
    )


def _strategy_sympy_verify(step_text, prev_text, reference_text, budget, **kwargs):
    from stem_tutor.graph.strategy import StrategyOutcome
    if not is_sympy_enabled():
        return StrategyOutcome(None, "failed", 0.0, metadata={"sympy_disabled": True})
    try:
        from stem_tutor.sympy_verify import sympy_verify_step
        result = sympy_verify_step(step_text, prev_text, reference_text)
        if result is None:
            return StrategyOutcome(None, "failed", 0.0, metadata={"sympy_inconclusive": True})
        label = VerificationLabel.CORRECT if result else VerificationLabel.INCORRECT_MATH
        return StrategyOutcome(
            {"label": label, "evidence": "SymPy 符号验证" + ("通过" if result else "检测到不等价"),
             "confidence": 0.95, "violated_principles": []},
            "full", 0.95,
            metadata={"sympy_verified": True, "sympy_equivalent": result},
        )
    except Exception as e:
        return StrategyOutcome(None, "failed", 0.0, metadata={"sympy_error": str(e)})


def _strategy_numerical_verify(step_text, prev_text, reference_answer_hint, budget, **kwargs):
    from stem_tutor.graph.strategy import StrategyOutcome
    if budget.remaining() < 3:
        return StrategyOutcome(None, "failed", 0.0)

    if not reference_answer_hint:
        return StrategyOutcome(None, "failed", 0.0, metadata={"no_reference_hint": True})

    ref_values = _extract_numeric_values(reference_answer_hint)
    step_values = _extract_numeric_values(step_text)

    if not ref_values or not step_values:
        return StrategyOutcome(None, "failed", 0.0, metadata={"no_numeric_values": True})

    last_step_val = step_values[-1]
    last_ref_val = ref_values[-1]

    match = abs(last_step_val - last_ref_val) < 1e-6
    if not match:
        return StrategyOutcome(
            {"label": VerificationLabel.INCORRECT_MATH,
             "evidence": f"数值校验：步骤最终值 {last_step_val:.6g} != 参考值 {last_ref_val:.6g}",
             "confidence": 0.85,
             "violated_principles": ["final_answer_numeric_mismatch"]},
            "full", 0.85,
            metadata={"method": "numerical_spot-check", "match": False},
        )

    return StrategyOutcome(
        {"label": VerificationLabel.CORRECT,
         "evidence": f"数值校验通过：{last_step_val:.6g} ~= {last_ref_val:.6g}",
         "confidence": 0.7,
         "violated_principles": []},
        "full", 0.7,
        metadata={"method": "numerical-spot-check", "match": True},
    )


def _strategy_agent_verify(
    step_text, prev_text, reference_text, problem_text,
    full_solution, step_id, total_steps, assertions,
    final_answer_status, reference_answer_hint,
    computation_hints, provider, budget, **kwargs
):
    from stem_tutor.graph.strategy import StrategyOutcome
    if not budget.can_make_tool_call():
        return StrategyOutcome(None, "failed", 0.0, metadata={"no_tool_budget": True})

    has_hints = bool(computation_hints) or bool(reference_answer_hint)
    max_iterations = 1 if has_hints else min(2, budget._config.max_tool_rounds)

    with budget.tool_execution_context() as timeout:
        raw, step_tool_calls = _verify_step_via_agent(
            problem_text=problem_text, reference_solution=reference_text,
            step_text=step_text, step_id=step_id, total_steps=total_steps,
            full_solution=full_solution, prev_text=prev_text, next_text=kwargs.get("next_text", ""),
            assertions=assertions, final_answer_status=final_answer_status,
            reference_answer_hint=reference_answer_hint, computation_hints=computation_hints,
        )

    for tc in step_tool_calls:
        budget.record_tool_call(timeout)

    return StrategyOutcome(
        raw, "full", 0.8,
        metadata={"tool_calls": step_tool_calls},
    )


def _strategy_pure_llm_verify(
    step_text, prev_text, reference_text, problem_text,
    full_solution, step_id, total_steps, assertions,
    final_answer_status, reference_answer_hint,
    computation_hints, provider, budget, **kwargs
):
    from stem_tutor.graph.strategy import StrategyOutcome
    prompt = verification_prompt(
        problem_text=problem_text, reference_solution=reference_text,
        step_text=step_text, step_id=step_id, total_steps=total_steps,
        full_solution=full_solution, prev_text=prev_text, next_text=kwargs.get("next_text", ""),
        assertions=assertions, final_answer_status=final_answer_status,
        reference_answer_hint=reference_answer_hint, computation_hints=computation_hints,
    )
    raw, schema_fallback = _verify_payload_with_retry(provider, prompt, retries=1)
    return StrategyOutcome(
        raw,
        "degraded",
        0.6,
        metadata={"pure_llm": True, "verify_schema_fallback": schema_fallback},
    )


def _run_new_verify_path(provider: LLMProvider, state: TutorGraphState) -> TutorGraphState:
    from stem_tutor.graph.budget import NodeBudgetManager, load_budget_config
    from stem_tutor.graph.strategy import StrategyChain

    problem_text = state["problem_input"].problem_text
    reference_text = state["reference_solution"]["reference_text"]
    assertions = state["reference_solution"].get("key_assertions", [])
    results: list[VerificationResult] = []
    low_conf_count = 0
    flags: list[str] = list(state.get("uncertainty_flags", []))
    run_meta = dict(state.get("run_meta", {}))

    logging.info(f"[verify_steps] [budget] Starting verification, reference_text length: {len(reference_text)}")

    if reference_text.startswith("Reference solution unavailable"):
        logging.warning("[verify_steps] [budget] Reference solution generation failed")
        flags.append("reference_solution_failed")

    steps = state["normalized_steps"]
    if not steps:
        logging.warning("[verify_steps] [budget] No steps to verify")
        flags.append("no_steps_to_verify")
        trace = state.get("trace", [])
        trace.append("verify_steps: no steps to verify")
        return {
            "verification_results": [],
            "uncertainty_flags": flags,
            "trace": trace,
            "run_meta": run_meta,
            "fail_reason": "没有可验证的解题步骤。",
        }

    budget_meta = state.get("budget_metadata", {})
    depth = budget_meta.get("depth", "with_ref")
    complexity = budget_meta.get("complexity", "moderate")

    subject_overrides = None
    try:
        ctx = get_subject_context()
        subject_overrides = ctx.budget_overrides
    except Exception:
        pass

    config = load_budget_config(depth, "verify", subject_overrides=subject_overrides)

    gb_dict = state.get("global_budget")
    if gb_dict:
        from stem_tutor.graph.global_budget import GlobalBudgetState
        gb = GlobalBudgetState.from_dict(gb_dict)
        verify_wall = gb.verify_available()
        is_critical = gb.critical_mode()
        per_step_budget = gb.per_step_budget()
    else:
        gb = None
        verify_wall = config.max_wall_seconds
        is_critical = False
        per_step_budget = config.max_wall_seconds / max(1, len(normal_steps))

    final_answer_verified = False
    final_answer_correct = None
    final_answer_step = None
    normal_steps = []

    for step in steps:
        if _is_final_answer_step(step):
            final_answer_step = step
        else:
            normal_steps.append(step)

    if final_answer_step:
        answer_text = final_answer_step.normalized_text.split(":", 1)[-1].strip()
        logging.info(f"[verify_steps] [budget] Verifying final answer: {answer_text[:50]}...")
        fa_result = _verify_final_answer(provider, problem_text, answer_text)
        if fa_result is not None:
            final_answer_verified = True
            final_answer_correct = fa_result.get("is_correct")
            fa_label = VerificationLabel.CORRECT if final_answer_correct else VerificationLabel.INCORRECT_MATH
            fa_evidence = fa_result.get("explanation", "")
            if not final_answer_correct and fa_result.get("correct_answer"):
                fa_evidence += f" 正确答案应为: {fa_result['correct_answer']}"
            results.append(VerificationResult(
                step_id=final_answer_step.step_id,
                label=fa_label,
                evidence=fa_evidence,
                confidence=0.9 if final_answer_verified else 0.5,
                violated_principles=[] if final_answer_correct else ["final_answer_incorrect"],
            ))

    full_solution = "\n".join([
        f"{step.step_id}: {step.normalized_text}"
        for step in normal_steps
    ])

    reference_answer_hint = _extract_reference_answer_hint(reference_text, assertions)
    computation_hints = state.get("reference_computation_hints", "")
    prev_result_hint_from_qs = ""
    quality_signals = state.get("quality_signals", {})
    if quality_signals.get("reference_quality") in ("degraded", "minimal"):
        prev_result_hint_from_qs = (
            f"\n[质量警告] 参考解答质量为 '{quality_signals.get('reference_quality')}'"
            f"（由 '{quality_signals.get('reference_strategy')}' 策略生成），请格外审慎验证。\n"
        )

    final_answer_status = ""
    if final_answer_verified:
        final_answer_status = "CORRECT" if final_answer_correct else "INCORRECT"

    tool_calling_enabled = _is_tool_calling_enabled()

    def _quick_llm_verify(
        provider, step, problem_text, reference_text, assertions,
        reference_answer_hint, computation_hints, full_solution,
        final_answer_status, prev_result,
    ) -> VerificationResult:
        idx_in_list = next((i for i, s in enumerate(normal_steps) if s.step_id == step.step_id), 0)
        prev_text = normal_steps[idx_in_list - 1].normalized_text if idx_in_list > 0 else ""
        next_text = normal_steps[idx_in_list + 1].normalized_text if idx_in_list < len(normal_steps) - 1 else ""
        prompt = verification_prompt(
            problem_text=problem_text, reference_solution=reference_text,
            step_text=step.normalized_text, step_id=step.step_id,
            total_steps=len(normal_steps), full_solution=full_solution,
            prev_text=prev_text, next_text=next_text,
            assertions=assertions, final_answer_status=final_answer_status,
            reference_answer_hint=reference_answer_hint,
            computation_hints=computation_hints,
        )
        raw = provider.verify_step(prompt)
        if isinstance(raw, dict):
            label_str = raw.get("label", "unclear")
            label_map = {
                "correct": VerificationLabel.CORRECT,
                "incorrect_math": VerificationLabel.INCORRECT_MATH,
                "inconsistent_or_unsupported": VerificationLabel.INCONSISTENT_OR_UNSUPPORTED,
                "unclear": VerificationLabel.UNCLEAR,
            }
            return VerificationResult(
                step_id=step.step_id,
                label=label_map.get(label_str, VerificationLabel.UNCLEAR),
                evidence=raw.get("evidence", ""),
                confidence=raw.get("confidence", 0.5),
                violated_principles=raw.get("violated_principles", []),
            )
        return VerificationResult(
            step_id=step.step_id,
            label=VerificationLabel.UNCLEAR,
            evidence="预算不足，仅执行快速验证。",
            confidence=0.3,
            violated_principles=["verification_budget_limited"],
        )

    def _verify_single_step_budget(idx: int, step, step_budget: NodeBudgetManager, prev_result: VerificationResult | None = None) -> tuple[VerificationResult, list[str], dict, bool, list[dict]]:
        prev_text = normal_steps[idx - 1].normalized_text if idx > 0 else ""
        next_text = normal_steps[idx + 1].normalized_text if idx < len(normal_steps) - 1 else ""

        extra_context = prev_result_hint_from_qs
        if prev_result and prev_result.label == VerificationLabel.CORRECT:
            extra_context += f"\n[已确认] 上一步验证结果: 正确（置信度 {prev_result.confidence}）\n"

        effective_computation_hints = computation_hints
        if extra_context:
            effective_computation_hints = (computation_hints + "\n" + extra_context).strip() if computation_hints else extra_context.strip()

        strategies = [
            ("sympy", _strategy_sympy_verify),
            ("numerical", _strategy_numerical_verify),
        ]
        if tool_calling_enabled:
            strategies.append(("tool_agent", _strategy_agent_verify))
        strategies.append(("pure_llm", _strategy_pure_llm_verify))

        chain = StrategyChain(strategies, step_budget)
        outcome = chain.execute(
            step_text=step.normalized_text,
            prev_text=prev_text,
            reference_text=reference_text,
            reference_answer_hint=reference_answer_hint,
            problem_text=problem_text,
            full_solution=full_solution,
            step_id=step.step_id,
            total_steps=len(normal_steps),
            assertions=assertions,
            final_answer_status=final_answer_status,
            computation_hints=effective_computation_hints,
            provider=provider,
            next_text=next_text,
        )

        result = _outcome_to_verification_result(outcome, step.step_id)

        adjusted_label, adjusted_evidence, adjusted_principles = _rule_based_adjustment(step.normalized_text)
        if adjusted_label is not None:
            if result.label != adjusted_label:
                final_conf = min(result.confidence, 0.6)
            else:
                final_conf = result.confidence
            result = VerificationResult(
                step_id=result.step_id,
                label=adjusted_label,
                evidence=adjusted_evidence,
                confidence=final_conf,
                violated_principles=sorted(set(result.violated_principles + adjusted_principles)),
                sympy_verified=result.sympy_verified,
                sympy_equivalent=result.sympy_equivalent,
            )

        sub_flags: list[str] = []
        sub_meta: dict = {}
        if outcome.metadata.get("verify_schema_fallback"):
            sub_flags.append("verify_schema_fallback")
            sub_flags.append("verify_schema_validation")
        is_low_conf = result.confidence < LOW_CONF_THRESHOLD
        tool_calls = outcome.metadata.get("tool_calls", []) if outcome.metadata else []
        return result, sub_flags, sub_meta, is_low_conf, tool_calls

    new_tool_logs: list[dict] = []

    if len(normal_steps) <= SEQUENTIAL_THRESHOLD:
        logging.info(f"[verify_steps] [budget] Sequential mode: {len(normal_steps)} steps")
        node_budget = NodeBudgetManager(config=config, complexity=complexity)
        total_steps = len(normal_steps)
        prev_result = None
        batch_done = False

        for idx, step in enumerate(normal_steps):
            remaining_after = total_steps - idx - 1

            if gb:
                current_step_budget = gb.verify_available() / max(1, remaining_after + 1)
            else:
                current_step_budget = node_budget.remaining() / max(1, remaining_after + 1)

            step_start = _time.perf_counter()

            if current_step_budget >= 15 and not is_critical:
                result, sub_flags, sub_meta, is_low_conf, step_tool_calls = _verify_single_step_budget(
                    idx, step, node_budget, prev_result=prev_result,
                )
                results.append(result)
                flags.extend(sub_flags)
                if is_low_conf:
                    low_conf_count += 1
                prev_result = result

                for tc_idx, tc in enumerate(step_tool_calls):
                    new_tool_logs.append({
                        "node": "verify_steps",
                        "step_id": result.step_id,
                        "tool_name": tc.get("tool_name", ""),
                        "call_index": tc_idx + 1,
                        "code": tc.get("code", ""),
                        "result_preview": tc.get("result_preview", ""),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })

            elif current_step_budget >= 6:
                result = _quick_llm_verify(
                    provider, step, problem_text, reference_text, assertions,
                    reference_answer_hint, computation_hints, full_solution,
                    final_answer_status, prev_result,
                )
                result = VerificationResult(
                    step_id=result.step_id,
                    label=result.label,
                    evidence=result.evidence,
                    confidence=max(result.confidence, 0.4),
                    violated_principles=result.violated_principles + ["verification_budget_limited"],
                )
                results.append(result)
                if result.confidence < LOW_CONF_THRESHOLD:
                    low_conf_count += 1
                prev_result = result
                logging.info(f"[verify_steps] [budget] Level 1 (quick-LLM) for step {step.step_id}")

            elif current_step_budget >= 3:
                result = _rule_based_fallback_verify(step)
                results.append(result)
                if result.confidence < LOW_CONF_THRESHOLD:
                    low_conf_count += 1
                prev_result = result
                logging.info(f"[verify_steps] [budget] Level 2 (rule-based) for step {step.step_id}")

            elif not batch_done:
                batch_results = _batch_verify_steps(
                    normal_steps[idx:], provider, problem_text, reference_text,
                    assertions, reference_answer_hint, full_solution,
                )
                results.extend(batch_results)
                low_conf_count += sum(1 for r in batch_results if r.confidence < LOW_CONF_THRESHOLD)
                batch_done = True
                logging.info(f"[verify_steps] [budget] Level 3 (batch) for steps {[s.step_id for s in normal_steps[idx:]]}")
                break

            else:
                result = _default_label_step(step)
                results.append(result)
                if result.confidence < LOW_CONF_THRESHOLD:
                    low_conf_count += 1
                prev_result = result
                logging.info(f"[verify_steps] [budget] Level 4 (default) for step {step.step_id}")

            step_elapsed = _time.perf_counter() - step_start
            if gb:
                gb.verify_used += step_elapsed
    else:
        worker_count = min(MAX_VERIFY_WORKERS, len(normal_steps))
        logging.info(f"[verify_steps] [budget] Parallel mode: {len(normal_steps)} steps, {worker_count} workers")

        def _verify_parallel(idx_step):
            idx, step = idx_step
            step_budget = NodeBudgetManager(config=config, complexity=complexity)
            return _verify_single_step_budget(idx, step, step_budget)

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_to_idx = {
                executor.submit(_verify_parallel, (idx, step)): idx
                for idx, step in enumerate(normal_steps)
            }

            for future in as_completed(future_to_idx):
                result, sub_flags, sub_meta, is_low_conf, step_tool_calls = future.result()
                results.append(result)
                flags.extend(sub_flags)
                if is_low_conf:
                    low_conf_count += 1
                for tc_idx, tc in enumerate(step_tool_calls):
                    new_tool_logs.append({
                        "node": "verify_steps",
                        "step_id": result.step_id,
                        "tool_name": tc.get("tool_name", ""),
                        "call_index": tc_idx + 1,
                        "code": tc.get("code", ""),
                        "result_preview": tc.get("result_preview", ""),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })

    results.sort(key=lambda r: r.step_id)

    ref_numeric = _extract_numeric_values(_extract_reference_answer_hint(reference_text, assertions)) if reference_answer_hint else []
    if ref_numeric:
        all_student_values: list[float] = []
        for step in normal_steps:
            all_student_values.extend(_extract_numeric_values(step.normalized_text))
        all_correct = all(r.label == VerificationLabel.CORRECT for r in results)
        answer_mismatch = not any(abs(v - ref_numeric[-1]) < 1e-6 for v in all_student_values)
        if all_correct and answer_mismatch:
            last_numeric_step_id = None
            for step in reversed(normal_steps):
                if _extract_numeric_values(step.normalized_text):
                    last_numeric_step_id = step.step_id
                    break
            if last_numeric_step_id:
                matched = False
                for i, r in enumerate(results):
                    if r.step_id == last_numeric_step_id:
                        results[i] = VerificationResult(
                            step_id=r.step_id,
                            label=VerificationLabel.INCORRECT_MATH,
                            evidence=(
                                f"数值一致性校验：学生最终数值结果与参考答案（~{ref_numeric[-1]:.6g}）不一致。"
                                f" {r.evidence}"
                            ),
                            confidence=0.9,
                            violated_principles=list(
                                set(r.violated_principles + ["final_answer_numeric_mismatch"])
                            ),
                            sympy_verified=r.sympy_verified,
                            sympy_equivalent=r.sympy_equivalent,
                        )
                        matched = True
                        break
                if not matched:
                    results.append(VerificationResult(
                        step_id=last_numeric_step_id,
                        label=VerificationLabel.INCORRECT_MATH,
                        evidence=(
                            f"数值一致性校验：学生最终数值结果与参考答案（~{ref_numeric[-1]:.6g}）不一致。"
                        ),
                        confidence=0.9,
                        violated_principles=["final_answer_numeric_mismatch", "verification_skipped_budget"],
                        sympy_verified=False,
                        sympy_equivalent=None,
                    ))
                flags.append("numeric_answer_override")

    if results and (low_conf_count / len(results)) > 0.4:
        if "too_many_low_confidence_steps" not in flags:
            flags.append("too_many_low_confidence_steps")
        if "manual_review_required" not in flags:
            flags.append("manual_review_required")

    sympy_count = sum(1 for r in results if r.sympy_verified)
    trace = state.get("trace", [])
    fa_status = ""
    if final_answer_verified:
        fa_status = f", final answer {'correct' if final_answer_correct else 'incorrect'}"
    trace.append(f"verify_steps [budget]: verified {len(results)} steps ({sympy_count} via SymPy){fa_status}")

    return_state = {
        "verification_results": results,
        "uncertainty_flags": flags,
        "trace": trace,
        "run_meta": run_meta,
        "fail_reason": (
            "Verification uncertainty too high; manual review required."
            if "too_many_low_confidence_steps" in set(flags)
            else state.get("fail_reason")
        ),
        "tool_calls_log": list(state.get("tool_calls_log", [])) + new_tool_logs,
    }

    if gb:
        gb.verify_used += gb.verify_available()
        return_state["global_budget"] = gb.to_dict()

    return return_state


def _rule_based_fallback_verify(step) -> VerificationResult:
    adjusted_label, adjusted_evidence, adjusted_principles = _rule_based_adjustment(step.normalized_text)
    if adjusted_label is not None:
        return VerificationResult(
            step_id=step.step_id,
            label=adjusted_label,
            evidence=adjusted_evidence or "基于规则的辅助验证。",
            confidence=0.45,
            violated_principles=adjusted_principles + ["verification_budget_limited"],
        )
    return VerificationResult(
        step_id=step.step_id,
        label=VerificationLabel.UNCLEAR,
        evidence="时间紧张，仅通过规则辅助验证，建议人工复核或点击重新验证。",
        confidence=0.3,
        violated_principles=["verification_budget_limited"],
    )


def _batch_verify_steps(steps, provider, problem_text, reference_text,
                        assertions, reference_answer_hint, full_solution) -> list[VerificationResult]:
    combined = "\n".join(f"[{s.step_id}] {s.normalized_text}" for s in steps)
    prompt = (
        f"题目：{problem_text}\n\n"
        f"参考解答：{reference_text[:500]}\n\n"
        f"请逐一判断以下步骤的数学推导是否正确。\n"
        f"对每个步骤，给出：step_id, label(correct/incorrect_math/unclear), evidence, confidence(0-1)。\n"
        f"返回 JSON 数组。\n\n步骤：\n{combined}"
    )
    results = []
    try:
        raw = provider.verify_step(prompt)
        if isinstance(raw, list):
            for item in raw:
                sid = item.get("step_id", "")
                matching = [s for s in steps if s.step_id == sid]
                if matching:
                    label_str = item.get("label", "unclear")
                    label_map = {
                        "correct": VerificationLabel.CORRECT,
                        "incorrect_math": VerificationLabel.INCORRECT_MATH,
                        "unclear": VerificationLabel.UNCLEAR,
                    }
                    results.append(VerificationResult(
                        step_id=sid,
                        label=label_map.get(label_str, VerificationLabel.UNCLEAR),
                        evidence=item.get("evidence", "批量验证模式，结果置信度较低。"),
                        confidence=item.get("confidence", 0.35),
                        violated_principles=item.get("violated_principles", []) + ["verification_batch_mode"],
                    ))
    except Exception as e:
        logging.warning(f"[verify_steps] Batch verify failed: {e}")

    for s in steps:
        if not any(r.step_id == s.step_id for r in results):
            results.append(_default_label_step(s))
    return results


def _default_label_step(step) -> VerificationResult:
    has_numbers = bool(_extract_numeric_values(step.normalized_text))
    has_equals = "=" in step.normalized_text
    if has_numbers and has_equals:
        return VerificationResult(
            step_id=step.step_id,
            label=VerificationLabel.UNCLEAR,
            evidence="因时间紧张未能完成验证，步骤包含计算内容，建议点击重新验证。",
            confidence=0.25,
            violated_principles=["verification_default_label"],
        )
    return VerificationResult(
        step_id=step.step_id,
        label=VerificationLabel.CORRECT,
        evidence="因时间紧张未能完成验证，步骤为纯文字描述，初步判断无计算错误。",
        confidence=0.3,
        violated_principles=["verification_default_label"],
    )


def make_verify_steps_node(provider: LLMProvider):
    def verify_steps_node(state: TutorGraphState) -> TutorGraphState:
        if _is_budget_enabled(state):
            return _run_new_verify_path(provider, state)

        import logging

        problem_text = state["problem_input"].problem_text
        reference_text = state["reference_solution"]["reference_text"]
        assertions = state["reference_solution"].get("key_assertions", [])
        results: list[VerificationResult] = []
        low_conf_count = 0
        flags: list[str] = list(state.get("uncertainty_flags", []))
        run_meta = dict(state.get("run_meta", {}))

        logging.info(f"[verify_steps] Starting verification, reference_text length: {len(reference_text)}")

        if reference_text.startswith("Reference solution unavailable"):
            logging.warning("[verify_steps] Reference solution generation failed, will verify without reference")
            flags.append("reference_solution_failed")
        else:
            logging.info("[verify_steps] Reference solution available")

        steps = state["normalized_steps"]

        if not steps:
            logging.warning("[verify_steps] No steps to verify")
            flags.append("no_steps_to_verify")
            trace = state.get("trace", [])
            trace.append("verify_steps: no steps to verify")
            return {
                "verification_results": [],
                "uncertainty_flags": flags,
                "trace": trace,
                "run_meta": run_meta,
                "fail_reason": "没有可验证的解题步骤。",
            }

        final_answer_verified = False
        final_answer_correct = None
        final_answer_step = None
        normal_steps = []

        for step in steps:
            if _is_final_answer_step(step):
                final_answer_step = step
            else:
                normal_steps.append(step)

        if final_answer_step:
            answer_text = final_answer_step.normalized_text.split(":", 1)[-1].strip()
            logging.info(f"[verify_steps] Verifying final answer: {answer_text[:50]}...")
            fa_result = _verify_final_answer(provider, problem_text, answer_text)
            if fa_result is not None:
                final_answer_verified = True
                final_answer_correct = fa_result.get("is_correct")
                fa_label = VerificationLabel.CORRECT if final_answer_correct else VerificationLabel.INCORRECT_MATH
                fa_evidence = fa_result.get("explanation", "")
                if not final_answer_correct and fa_result.get("correct_answer"):
                    fa_evidence += f" 正确答案应为: {fa_result['correct_answer']}"
                results.append(VerificationResult(
                    step_id=final_answer_step.step_id,
                    label=fa_label,
                    evidence=fa_evidence,
                    confidence=0.9 if final_answer_verified else 0.5,
                    violated_principles=[] if final_answer_correct else ["final_answer_incorrect"],
                ))
                logging.info(f"[verify_steps] Final answer verification: {final_answer_correct}")
            else:
                logging.warning("[verify_steps] Final answer verification returned None")

        full_solution = "\n".join([
            f"{step.step_id}: {step.normalized_text}"
            for step in normal_steps
        ])

        reference_answer_hint = _extract_reference_answer_hint(reference_text, assertions)
        computation_hints = state.get("reference_computation_hints", "")
        final_answer_status = ""
        if final_answer_verified:
            final_answer_status = "CORRECT" if final_answer_correct else "INCORRECT"

        def _verify_single_step(idx: int, step) -> tuple[VerificationResult, list[str], dict, bool, list[dict]]:
            prev_text = normal_steps[idx - 1].normalized_text if idx > 0 else ""
            next_text = normal_steps[idx + 1].normalized_text if idx < len(normal_steps) - 1 else ""

            try:
                from stem_tutor.settings import is_deterministic_verify_enabled
                if is_deterministic_verify_enabled():
                    det_result = _deterministic_pre_check(
                        step.normalized_text, prev_text, reference_answer_hint,
                    )
                    if det_result is not None:
                        label = VerificationLabel.CORRECT if det_result else VerificationLabel.INCORRECT_MATH
                        evidence = "确定性数值校验通过" if det_result else "确定性数值校验检测到不匹配"
                        logging.info(f"[verify_steps] Step {step.step_id} deterministic_pre_check: result={det_result}")
                        result = VerificationResult(
                            step_id=step.step_id,
                            label=label,
                            evidence=evidence,
                            confidence=0.95,
                            violated_principles=[] if det_result else ["numeric_mismatch"],
                        )
                        return result, [], {}, False, []
            except Exception as e:
                logging.debug(f"[verify_steps] Step {step.step_id} deterministic pre-check failed: {e}")

            if is_sympy_enabled():
                try:
                    from stem_tutor.sympy_verify import sympy_verify_step as _sympy_check
                    sympy_result = _sympy_check(
                        step_text=step.normalized_text,
                        prev_text=prev_text,
                        reference_text=reference_text,
                    )
                    if sympy_result is not None:
                        label = VerificationLabel.CORRECT if sympy_result else VerificationLabel.INCORRECT_MATH
                        evidence = "SymPy 符号验证通过" if sympy_result else "SymPy 检测到数学不等价"
                        logging.info(f"[verify_steps] Step {step.step_id} sympy_short_circuit: sympy_result={sympy_result}")
                        result = VerificationResult(
                            step_id=step.step_id,
                            label=label,
                            evidence=evidence,
                            confidence=0.95,
                            violated_principles=[] if sympy_result else ["symbolic_mismatch"],
                            sympy_verified=True,
                            sympy_equivalent=sympy_result,
                        )
                        return result, [], {}, False, []
                except Exception as e:
                    logging.debug(f"[verify_steps] Step {step.step_id} sympy check failed: {e}")

            tool_calling_enabled = _is_tool_calling_enabled()
            _step_started_at = _time.perf_counter()
            step_tool_calls: list[dict] = []
            schema_retry_fallback = False
            if tool_calling_enabled:
                try:
                    raw, step_tool_calls = _verify_step_via_agent(
                        problem_text=problem_text,
                        reference_solution=reference_text,
                        step_text=step.normalized_text,
                        step_id=step.step_id,
                        total_steps=len(normal_steps),
                        full_solution=full_solution,
                        prev_text=prev_text,
                        next_text=next_text,
                        assertions=assertions,
                        final_answer_status=final_answer_status,
                        reference_answer_hint=reference_answer_hint,
                        computation_hints=computation_hints,
                        model_name=getattr(provider, "model_name", None),
                    )
                    logging.info(f"[verify_steps] Step {step.step_id} verified via tool-calling agent")
                except Exception as e:
                    logging.warning(f"[verify_steps] Step {step.step_id} agent verify failed: {e}, falling back to standard")
                    tool_calling_enabled = False
                    step_tool_calls = []

            if not tool_calling_enabled:
                prompt = verification_prompt(
                    problem_text=problem_text,
                    reference_solution=reference_text,
                    step_text=step.normalized_text,
                    step_id=step.step_id,
                    total_steps=len(normal_steps),
                    full_solution=full_solution,
                    prev_text=prev_text,
                    next_text=next_text,
                    assertions=assertions,
                    final_answer_status=final_answer_status,
                    reference_answer_hint=reference_answer_hint,
                    computation_hints=computation_hints,
                )
                logging.info(f"[verify_steps] Verifying step {step.step_id}: {step.normalized_text[:60]}...")
                raw, schema_retry_fallback = _verify_payload_with_retry(provider, prompt, retries=1)

            local_schema_fallback = False
            try:
                payload = VerificationPayload(**raw)
                logging.info(f"[verify_steps] Step {step.step_id} parsed: label={payload.label.value}, conf={payload.confidence}")
            except ValidationError as e:
                logging.error(f"[verify_steps] Step {step.step_id} validation error: {e}")
                payload = VerificationPayload(
                    label=VerificationLabel.UNCLEAR,
                    evidence=f"验证输出格式错误: {str(e)[:100]}",
                    confidence=0.2,
                    violated_principles=["schema_validation"],
                )
                local_schema_fallback = True

            local_schema_fallback = local_schema_fallback or schema_retry_fallback

            sub_state: TutorGraphState = {
                "uncertainty_flags": [],
                "run_meta": {},
            }
            sub_flags, sub_meta = record_provider_call(
                sub_state,
                provider,
                node_name="verify",
                fallback_flag="verify_schema_fallback",
                local_schema_fallback=local_schema_fallback,
                started_at=_step_started_at,
            )

            adjusted_label, adjusted_evidence, adjusted_principles = _rule_based_adjustment(step.normalized_text)
            final_label = payload.label
            final_conf = payload.confidence
            final_evidence = payload.evidence
            final_principles = list(payload.violated_principles)

            if adjusted_label is not None:
                if payload.label != adjusted_label:
                    sub_flags.append("verify_rule_model_disagreement")
                    final_conf = min(final_conf, 0.6)
                final_label = adjusted_label
                final_evidence = adjusted_evidence
                final_principles = sorted(set(final_principles + adjusted_principles))

            result = VerificationResult(
                step_id=step.step_id,
                label=final_label,
                evidence=final_evidence,
                confidence=final_conf,
                violated_principles=final_principles,
                sympy_verified=False,
                sympy_equivalent=None,
            )
            is_low_conf = result.confidence < LOW_CONF_THRESHOLD
            return result, sub_flags, sub_meta, is_low_conf, step_tool_calls

        worker_count = min(MAX_VERIFY_WORKERS, len(normal_steps))
        logging.info(f"[verify_steps] Running {len(normal_steps)} verifications with {worker_count} workers")

        new_tool_logs: list[dict] = []

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_to_idx = {
                executor.submit(_verify_single_step, idx, step): idx
                for idx, step in enumerate(normal_steps)
            }

            for future in as_completed(future_to_idx):
                result, sub_flags, sub_meta, is_low_conf, step_tool_calls = future.result()
                results.append(result)
                flags.extend(sub_flags)
                if is_low_conf:
                    low_conf_count += 1
                for k, v in sub_meta.get("node_stats", {}).items():
                    run_meta.setdefault("node_stats", {}).setdefault(k, {"provider_calls": 0, "fallback_calls": 0, "retry_sum": 0})
                    for mk, mv in v.items():
                        run_meta["node_stats"][k][mk] = run_meta["node_stats"][k].get(mk, 0) + mv
                run_meta.setdefault("provider_events", []).extend(sub_meta.get("provider_events", []))
                for tc_idx, tc in enumerate(step_tool_calls):
                    new_tool_logs.append({
                        "node": "verify_steps",
                        "step_id": result.step_id,
                        "tool_name": tc.get("tool_name", ""),
                        "call_index": tc_idx + 1,
                        "code": tc.get("code", ""),
                        "result_preview": tc.get("result_preview", ""),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })

        results.sort(key=lambda r: r.step_id)

        ref_numeric = _extract_numeric_values(_extract_reference_answer_hint(reference_text, assertions)) if reference_answer_hint else []
        if ref_numeric:
            all_student_values: list[float] = []
            for step in normal_steps:
                all_student_values.extend(_extract_numeric_values(step.normalized_text))
            all_correct = all(r.label == VerificationLabel.CORRECT for r in results)
            answer_mismatch = not any(abs(v - ref_numeric[-1]) < 1e-6 for v in all_student_values)
            if all_correct and answer_mismatch:
                last_numeric_step_id = None
                for step in reversed(normal_steps):
                    if _extract_numeric_values(step.normalized_text):
                        last_numeric_step_id = step.step_id
                        break
                if last_numeric_step_id:
                    for i, r in enumerate(results):
                        if r.step_id == last_numeric_step_id:
                            results[i] = VerificationResult(
                                step_id=r.step_id,
                                label=VerificationLabel.INCORRECT_MATH,
                                evidence=(
                                    f"数值一致性校验：学生最终数值结果与参考答案（~{ref_numeric[-1]:.6g}）不一致。"
                                    f" {r.evidence}"
                                ),
                                confidence=0.9,
                                violated_principles=list(
                                    set(r.violated_principles + ["final_answer_numeric_mismatch"])
                                ),
                                sympy_verified=r.sympy_verified,
                                sympy_equivalent=r.sympy_equivalent,
                            )
                            break
                    flags.append("numeric_answer_override")
                    logging.info(
                        f"[verify_steps] Numeric answer override: student values {all_student_values} "
                        f"do not match reference {ref_numeric[-1]}"
                    )

        if results and (low_conf_count / len(results)) > 0.4:
            if "too_many_low_confidence_steps" not in flags:
                flags.append("too_many_low_confidence_steps")
            if "manual_review_required" not in flags:
                flags.append("manual_review_required")

        sympy_count = sum(1 for r in results if r.sympy_verified)
        trace = state.get("trace", [])
        fa_status = ""
        if final_answer_verified:
            fa_status = f", final answer {'correct' if final_answer_correct else 'incorrect'}"
        trace.append(f"verify_steps: verified {len(results)} steps ({sympy_count} via SymPy){fa_status}")

        return {
            "verification_results": results,
            "uncertainty_flags": flags,
            "trace": trace,
            "run_meta": run_meta,
            "fail_reason": (
                "Verification uncertainty too high; manual review required."
                if "too_many_low_confidence_steps" in set(flags)
                else state.get("fail_reason")
            ),
            "tool_calls_log": list(state.get("tool_calls_log", [])) + new_tool_logs,
        }

    return verify_steps_node
