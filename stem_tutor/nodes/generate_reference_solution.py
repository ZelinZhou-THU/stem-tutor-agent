from __future__ import annotations

import logging
import os
import time as _time
from datetime import datetime, timezone

from pydantic import ValidationError

from stem_tutor.domain.models import ReferenceSolutionPayload
from stem_tutor.graph.agent_subgraph import SCHEMA_KEYS
from stem_tutor.graph.observability import record_provider_call
from stem_tutor.graph.state import TutorGraphState
from stem_tutor.providers.base import LLMProvider

_FAILED_HINT_PREFIXES = ("[Error Type:", "[Warning:", "Error:", "Traceback ", "RuntimeError", "TypeError", "TimeoutError")

_DEGRADED_MARKERS = (
    "代码执行超时",
    "超时了",
    "timed out",
    "reference solution unavailable",
    "unable to generate",
)

_META_THINKING_PREFIXES = (
    "我注意到",
    "让我",
    "我看到",
    "我需要",
    "看起来",
    "首先让我",
    "让我重新",
    "i notice",
    "i see",
    "let me",
    "looking at",
    "it seems",
)

_MATH_INDICATORS = ("=", "\\boxed", "$", "∫", "√", "frac", "**", "\\\\")


def _is_failed_preview(preview: str) -> bool:
    if not preview:
        return False
    return any(preview.strip().startswith(p) for p in _FAILED_HINT_PREFIXES)


def _looks_like_meta_thinking(text: str) -> bool:
    """Detect LLM output that looks like internal monologue rather than a
    structured reference solution. Real references are math-heavy and rarely
    start with first-person reflection.
    """
    if not text:
        return True
    stripped = text.lstrip()
    lowered = stripped.lower()
    if any(lowered.startswith(p) for p in _META_THINKING_PREFIXES):
        return True
    if len(text) > 100:
        math_count = sum(1 for tok in _MATH_INDICATORS if tok in text)
        if math_count == 0:
            return True
    return False


def _is_degraded(text: str) -> bool:
    """Detect low-quality reference text. Case-insensitive marker match so
    that ``_minimal_reference``'s capital-U "Unable to generate" is itself
    flagged as degraded (avoids shipping a degraded fallback as a valid
    answer)."""
    if not text or len(text) < 50:
        return True
    if _looks_like_meta_thinking(text):
        return True
    lowered = text.lower()
    return any(marker in lowered for marker in _DEGRADED_MARKERS)


def _is_tool_calling_enabled() -> bool:
    try:
        from stem_tutor.settings import is_tool_calling_enabled as _check
        return _check()
    except Exception:
        return False


def _is_budget_enabled(state: dict | None = None) -> bool:
    if state is not None and "budget_enabled" in state:
        return bool(state["budget_enabled"])
    import os
    val = os.environ.get("STEM_TUTOR_BUDGET_ENABLED", "").strip().lower()
    return val in {"1", "true", "yes", "on"}


def _generate_via_agent(problem_text: str, model_name: str | None = None, max_tool_rounds: int | None = None, subject_id: str = "calculus") -> tuple[dict, list[dict]]:
    from stem_tutor.graph.agent_subgraph import AgentSubgraph, parse_json_from_text
    from stem_tutor.settings import is_dual_model_enabled, load_provider_settings, reference_max_tool_rounds
    from stem_tutor.subjects.context import get_subject_context

    settings = load_provider_settings()
    ctx = get_subject_context(subject_id)
    display_name = ctx.display_name
    max_rounds = max_tool_rounds if max_tool_rounds is not None else reference_max_tool_rounds()

    system_prompt = (
        ctx.prompts["system_role"].replace("{subject_name}", display_name)
        + "\n\n"
        "=== 强制输出格式（最高优先级） ===\n"
        "你的最终回复必须且只能是一个 JSON 对象，不使用 markdown 代码块包裹。\n"
        "JSON 必须且只能包含以下两个键，缺一不可，且键名必须完全一致：\n"
        '  "reference_text": string（完整分步解答文本，使用 LaTeX 包裹数学公式）\n'
        '  "key_assertions": string[]（3-5 条关键断言）\n'
        "严禁使用其他键名（如 problem、solution、steps、solution_steps、verification、"
        "final_answer、decimal_value、explanation 等）。\n"
        "严禁在 JSON 之外输出任何文字、思考、解释或代码块。\n\n"
        "=== 工具调用规则 ===\n"
        "你可以使用 execute_python 工具执行 Python 代码进行精确计算（支持 sympy/numpy/scipy）。\n\n"
        "【严禁操作 - blacklist=v1】\n"
        "- 禁止使用 sp.integrate() 或 sympy.integrate() 计算定积分/不定积分，会导致超时。"
        "如需验证积分结果，用 scipy.integrate.quad() 做数值验证，或用 sp.beta/sp.gamma 计算特殊函数。\n"
        "- 禁止使用 sp.limit() 处理复杂极限（含嵌套根式/分数幂），用数值逼近替代。\n"
        "- 代码总长度控制在 800 字符以内，只保留核心计算逻辑。\n\n"
        "【计算规范 - efficiency=v2】\n"
        "- 选择最短、最直接的验证路径，避免过度计算。\n"
        "- 优先使用数值方法（scipy.integrate.quad, numpy）而非符号计算（sympy）。\n"
        "- 避免对简单问题使用多种方法重复验证。\n"
        "- 将所有计算写在同一次 execute_python 调用中，用 print() 输出每个关键步骤的结果。\n"
        "- 在代码中加入自验证：求导结果做积分回验、方程解做代入检验、极限结果做数值逼近确认等。\n"
        "- 如果自验证发现不一致，在代码内直接修正并重新计算，无需额外工具调用。\n\n"
        "【输出规范 - tool_prompt_policy=v1】\n"
        "- print() 输出优先使用 ASCII 字符，避免 Unicode 特殊字符（如 ∫、√、₀、¹ 等）。\n"
        "- 在代码末尾添加固定输出锚点，便于后处理识别：\n"
        "  FINAL_ANSWER=<最终答案或表达式>\n"
        "  CHECK_PASS=<true/false，表示自校验是否通过>\n"
        "  KEY_RESULT=<关键中间结果摘要>\n"
        "- 仅使用允许库：sympy、numpy、scipy、math、fractions、json，禁止其他第三方库。\n\n"
        "【多轮调用策略 - strict_cap=v1】\n"
        "- 优先一次 execute_python 调用完成全部计算。\n"
        "- 仅在第一次结果与预期严重不符且需要重新分析题意时才考虑第二次调用。\n"
        "- 若执行失败，不要重试，直接基于理论推导给出文字解答。\n"
    )

    resolved_model = model_name or settings.reasoning_model_name
    tool_model = settings.fast_model_name if is_dual_model_enabled() else None
    agent = AgentSubgraph(
        api_key=settings.api_key,
        base_url=settings.base_url,
        model_name=resolved_model,
        system_prompt=system_prompt,
        max_tokens=16000,
        tool_model_name=tool_model,
        response_format={"type": "json_object"},
    )

    agent_result = agent.invoke(
        f"题目: {problem_text}\n\n请提供完整的分步解答。最终只返回包含 reference_text 和 key_assertions 两个键的 JSON 对象。",
        max_iterations=max_rounds,
    )

    messages = agent_result.messages
    tool_call_count = sum(
        1 for m in messages if hasattr(m, "tool_calls") and m.tool_calls
    )
    logging.info(
        f"[generate_reference_solution] Agent used {tool_call_count} tool calls across {len(messages)} messages"
    )

    last_ai = agent.get_last_ai_message(messages)
    if last_ai is None:
        raise ValueError("Agent produced no AI message")

    raw = parse_json_from_text(last_ai.content)
    if "reference_text" not in raw:
        # Detect the "router exited via success_anchor but produced no schema"
        # failure mode: the LLM output prose containing anchor strings rather
        # than schema JSON. treat as failure so the caller can fall back to
        # the minimal reference instead of shipping meta-thinking as the answer.
        if agent_result.termination_reason == "success_anchor" and not _looks_like_schema(raw):
            raise ValueError(
                f"Agent exited via success_anchor but produced no schema JSON. "
                f"raw_keys={list(raw.keys())}"
            )
        if last_ai.content and _looks_like_meta_thinking(last_ai.content):
            raise ValueError(
                f"Agent output looks like meta-thinking, not a reference solution: "
                f"{last_ai.content[:200]}"
            )
        raise ValueError(f"Could not extract reference_text from agent response: {list(raw.keys())}")

    raw.setdefault("key_assertions", [])
    return raw, agent_result.tool_calls


def _looks_like_schema(raw: dict) -> bool:
    """Heuristic: does raw already look like a valid reference / verify / diagnosis payload?"""
    if not isinstance(raw, dict):
        return False
    return any(key in raw for key in SCHEMA_KEYS)


def _minimal_reference(problem: str) -> dict:
    return {
        "reference_text": f"Unable to generate reference solution for: {problem}",
        "key_assertions": [],
    }


def _strategy_tool_agent(problem, provider, state, budget, **kwargs):
    if not budget.can_make_tool_call():
        from stem_tutor.graph.strategy import StrategyOutcome
        return StrategyOutcome(None, "failed", 0.0, metadata={"skipped": True, "reason": "no_tool_budget"})

    with budget.tool_execution_context() as timeout:
        remaining_rounds = budget._config.max_tool_rounds - budget._tool_rounds_used
        subject_id = state.get("subject_id", "calculus") if isinstance(state, dict) else "calculus"
        raw, tool_calls = _generate_via_agent(problem, max_tool_rounds=max(1, remaining_rounds), subject_id=subject_id)

    for tc in tool_calls:
        elapsed = tc.get("elapsed_seconds", timeout)
        if tc.get("result_preview", "").startswith("[Error Type: Timeout"):
            elapsed = timeout
        budget.record_tool_call(elapsed)

    ref_text = raw.get("reference_text", "")
    from stem_tutor.graph.strategy import StrategyOutcome
    if not ref_text or _is_degraded(ref_text):
        return StrategyOutcome(
            raw, "degraded", 0.3,
            metadata={"degradation_reason": "empty_or_timeout", "tool_calls": tool_calls},
        )

    return StrategyOutcome(
        raw, "full", 0.9,
        metadata={"tool_calls": tool_calls},
    )


def _strategy_text_llm(problem, provider, state, budget, **kwargs):
    from stem_tutor.graph.strategy import StrategyOutcome
    raw = provider.generate_reference_solution(problem)
    ref_text = raw.get("reference_text", "")

    if not ref_text or len(ref_text) < 50:
        return StrategyOutcome(raw, "degraded", 0.2)

    has_answer = "\\boxed{" in ref_text or any(
        c.isdigit() for c in ref_text[-100:]
    )
    quality = "degraded"
    confidence = 0.5
    if has_answer:
        quality = "degraded"
        confidence = 0.65

    return StrategyOutcome(
        raw, quality, confidence,
        metadata={"no_tool_verification": True},
    )


def _strategy_template(problem, provider, state, budget, **kwargs):
    from stem_tutor.graph.strategy import StrategyOutcome
    try:
        from stem_tutor.subjects.context import get_subject_context
        subject_id = state.get("subject_id", "calculus") if isinstance(state, dict) else "calculus"
        ctx = get_subject_context(subject_id)
        template = ctx.mock_reference_solution
        return StrategyOutcome(
            template, "minimal", 0.2,
            metadata={"template_fallback": True},
        )
    except Exception:
        return StrategyOutcome(
            {"reference_text": f"Unable to generate reference solution for: {problem}", "key_assertions": []},
            "minimal", 0.1,
        )


def _build_computation_hints(tool_calls: list[dict], outcome) -> str:
    from stem_tutor.graph.strategy import StrategyOutcome
    parts: list[str] = []

    for idx, tc in enumerate(tool_calls):
        preview = tc.get("result_preview", "")
        if preview and not preview.startswith("[Error"):
            parts.append(f"[计算结果 {idx+1}] {preview}")

    if not parts:
        failed = sum(1 for tc in tool_calls if tc.get("result_preview", "").startswith("[Error"))
        timeout_count = sum(1 for tc in tool_calls if "Timeout" in tc.get("result_preview", ""))
        if failed > 0:
            msg = f"[参考阶段信息] Python 工具调用 {failed} 次均失败"
            if timeout_count > 0:
                msg += f"（其中 {timeout_count} 次超时）"
            msg += "。验证阶段请自行使用数值方法独立验证。"
            parts.append(msg)

    if outcome.quality in ("degraded", "minimal"):
        parts.append(
            f"[质量警告] 参考解答由 '{outcome.strategy_name}' 策略生成"
            f"（质量={outcome.quality}），验证阶段请格外审慎。"
        )

    return "\n".join(parts) if parts else ""


def _run_new_path(provider: LLMProvider, state: TutorGraphState) -> TutorGraphState:
    from stem_tutor.graph.budget import NodeBudgetManager, load_budget_config
    from stem_tutor.graph.strategy import StrategyChain

    problem = state["problem_input"].problem_text
    logging.info(f"[generate_reference_solution] [budget] Generating solution for: {problem[:80]}...")

    budget_meta = state.get("budget_metadata", {})
    depth = budget_meta.get("depth", "with_ref")
    complexity = budget_meta.get("complexity", "moderate")

    subject_overrides = None
    try:
        from stem_tutor.subjects.context import get_subject_context
        subject_id = state.get("subject_id", "calculus")
        ctx = get_subject_context(subject_id)
        subject_overrides = ctx.budget_overrides
    except Exception:
        pass

    config = load_budget_config(depth, "reference", subject_overrides=subject_overrides)

    gb_dict = state.get("global_budget")
    if gb_dict:
        from stem_tutor.graph.budget import budget_from_global
        config = budget_from_global(gb_dict, "reference")

    budget = NodeBudgetManager(config=config, complexity=complexity)

    tool_calling_enabled = _is_tool_calling_enabled()
    strategies = []
    if tool_calling_enabled:
        strategies.append(("tool_agent", _strategy_tool_agent))
    strategies.append(("text_llm", _strategy_text_llm))
    strategies.append(("template", _strategy_template))

    chain = StrategyChain(strategies, budget)
    _started_at = _time.perf_counter()
    outcome = chain.execute(
        problem=problem,
        provider=provider,
        state=state,
    )

    quality_signals = {
        "reference_quality": outcome.quality,
        "reference_strategy": outcome.strategy_name,
        "reference_elapsed": round(outcome.elapsed_seconds, 1),
        "reference_confidence": outcome.confidence,
    }

    raw = outcome.data or {}
    ref_text = raw.get("reference_text", "")
    if (not ref_text or _is_degraded(ref_text)) and outcome.confidence < 0.5:
        try:
            logging.info("[generate_reference_solution] [budget] Strategy chain yielded low quality, trying direct provider fallback")
            fallback_raw = provider.generate_reference_solution(problem)
            fallback_text = fallback_raw.get("reference_text", "")
            if fallback_text and len(fallback_text) > 80 and not _is_degraded(fallback_text):
                raw = fallback_raw
                quality_signals["reference_strategy"] += "+fallback_llm"
                quality_signals["reference_quality"] = "degraded"
                quality_signals["reference_confidence"] = 0.55
                logging.info("[generate_reference_solution] [budget] Provider fallback succeeded")
        except Exception as e:
            logging.warning(f"[generate_reference_solution] [budget] Provider fallback also failed: {e}")

    ref_text = raw.get("reference_text", "")
    if not ref_text or _is_degraded(ref_text):
        raw = _minimal_reference(problem)
        quality_signals["reference_quality"] = "minimal"

    local_schema_fallback = False
    try:
        parsed = ReferenceSolutionPayload(**raw)
        # Re-check for degradation after Pydantic coercion: the pre-Pydantic
        # check can miss meta-thinking replies that happen to include an "="
        # or other math indicator, and the legacy prefix check below only
        # matches the original "Reference solution unavailable" string,
        # missing the newer "Unable to generate" minimal reference prefix.
        if parsed.reference_text and not (
            parsed.reference_text.startswith("Reference solution unavailable")
            or _is_degraded(parsed.reference_text)
        ):
            logging.info("[generate_reference_solution] [budget] Successfully generated reference solution")
        else:
            if not parsed.reference_text:
                logging.warning("[generate_reference_solution] [budget] Generated reference is empty")
            else:
                logging.warning(
                    f"[generate_reference_solution] [budget] Generated reference is degraded "
                    f"(len={len(parsed.reference_text)}, prefix={parsed.reference_text[:30]!r})"
                )
            parsed = ReferenceSolutionPayload(**_minimal_reference(problem))
            local_schema_fallback = True
    except ValidationError as e:
        logging.error(f"[generate_reference_solution] [budget] Validation error: {e}")
        parsed = ReferenceSolutionPayload(**_minimal_reference(problem))
        local_schema_fallback = True

    flags, run_meta = record_provider_call(
        state,
        provider,
        node_name="reference",
        fallback_flag="reference_schema_fallback",
        local_schema_fallback=local_schema_fallback,
        started_at=_started_at,
    )

    run_meta.setdefault("node_stats", {}).setdefault("reference", {})["strategy"] = outcome.strategy_name
    run_meta["node_stats"]["reference"]["quality"] = outcome.quality

    tool_calls = outcome.metadata.get("tool_calls", [])
    hints = _build_computation_hints(tool_calls, outcome)
    if not hints:
        hints = _build_computation_hints_legacy(tool_calls)

    # Record the final reference quality signal so audit logs and the
    # frontend can surface degraded references.
    ref_text_for_signal = parsed.reference_text if hasattr(parsed, "reference_text") else ""
    quality_signals["reference_is_degraded"] = (
        _is_degraded(ref_text_for_signal) if ref_text_for_signal else True
    )

    trace = state.get("trace", [])
    if local_schema_fallback:
        trace.append(f"generate_reference_solution ({outcome.strategy_name}): reference generation failed (fallback)")
    else:
        trace.append(f"generate_reference_solution ({outcome.strategy_name}): reference generated")

    existing_logs = list(state.get("tool_calls_log", []))
    new_tool_logs = []
    for idx, tc in enumerate(tool_calls):
        preview = tc.get("result_preview", "")
        new_tool_logs.append({
            "node": "generate_reference_solution",
            "step_id": None,
            "tool_name": tc.get("tool_name", ""),
            "call_index": idx + 1,
            "code": tc.get("code", ""),
            "result_preview": preview,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    return_state = {
        "reference_solution": parsed.model_dump(),
        "reference_computation_hints": hints,
        "uncertainty_flags": flags,
        "trace": trace,
        "run_meta": run_meta,
        "tool_calls_log": existing_logs + new_tool_logs,
        "quality_signals": quality_signals,
        "budget_metadata": {**budget_meta, "reference_budget": budget.summary()},
    }

    if gb_dict:
        from stem_tutor.graph.global_budget import GlobalBudgetState
        gb = GlobalBudgetState.from_dict(gb_dict)
        gb.reference_used = budget.elapsed()
        return_state["global_budget"] = gb.to_dict()

    return return_state


def _build_computation_hints_legacy(tool_calls: list[dict]) -> str:
    computation_outputs: list[str] = []
    for idx, tc in enumerate(tool_calls):
        preview = tc.get("result_preview", "")
        if preview and not _is_failed_preview(preview):
            computation_outputs.append(f"[调用{idx + 1}] {preview}")

    if not computation_outputs:
        return ""

    from stem_tutor.settings import hint_max_chars
    max_chars = hint_max_chars()
    combined = "参考解答阶段的 Python 计算结果（可直接使用，无需重复计算）：\n" + "\n".join(computation_outputs)
    if len(combined) > max_chars:
        combined = combined[:max_chars] + "\n...(truncated)"
    return combined


def make_generate_reference_solution_node(provider: LLMProvider):
    def generate_reference_solution_node(state: TutorGraphState) -> TutorGraphState:
        from stem_tutor.prompts.templates import set_active_subject
        subject_id = state.get("subject_id", "calculus")
        set_active_subject(subject_id)
        depth = state.get("budget_metadata", {}).get("depth", "")
        if depth == "no_ref":
            state["reference_solution"] = {
                "reference_text": "Reference solution unavailable (no_ref mode)",
                "key_assertions": [],
            }
            return state

        if _is_budget_enabled(state):
            return _run_new_path(provider, state)

        problem = state["problem_input"].problem_text

        logging.info(f"[generate_reference_solution] Generating solution for: {problem[:80]}...")

        use_tools = _is_tool_calling_enabled()
        local_schema_fallback = False
        complexity_label = ""

        _started_at = _time.perf_counter()

        if use_tools:
            try:
                from stem_tutor.settings import is_simple_fastpath_enabled
                if is_simple_fastpath_enabled():
                    from stem_tutor.nodes.complexity_gate import classify_complexity, ProblemComplexity
                    from stem_tutor.settings import load_provider_settings
                    from stem_tutor.providers.factory import create_provider

                    settings = load_provider_settings()
                    classifier_provider = create_provider(
                        settings.provider_type, settings,
                        model_group="baseline", baseline_name="glm5",
                    )
                    complexity = classify_complexity(problem, classifier_provider)
                    complexity_label = complexity.value

                    if complexity == ProblemComplexity.SIMPLE:
                        logging.info("[generate_reference_solution] SIMPLE classification -> standard LLM path")
                        use_tools = False
            except Exception as e:
                logging.warning(f"[generate_reference_solution] Complexity classification failed: {e}, proceeding with agent")

        if use_tools:
            logging.info("[generate_reference_solution] Using tool-calling agent mode")
            try:
                model_name = getattr(provider, "model_name", None)
                subject_id = state.get("subject_id", "calculus")
                raw, agent_tool_calls = _generate_via_agent(problem, model_name=model_name, subject_id=subject_id)
                logging.info(f"[generate_reference_solution] Agent raw result keys: {list(raw.keys())}")
            except Exception as e:
                logging.warning(f"[generate_reference_solution] Agent failed, falling back to standard: {e}")
                raw = provider.generate_reference_solution(problem)
                use_tools = False
                agent_tool_calls = []
        else:
            raw = provider.generate_reference_solution(problem)
            agent_tool_calls = []
            logging.info(f"[generate_reference_solution] Raw result keys: {list(raw.keys()) if isinstance(raw, dict) else 'not dict'}")

        try:
            parsed = ReferenceSolutionPayload(**raw)
            if parsed.reference_text and not parsed.reference_text.startswith("Reference solution unavailable"):
                if _is_degraded(parsed.reference_text):
                    logging.warning(
                        f"[generate_reference_solution] LLM fallback produced degraded reference "
                        f"(len={len(parsed.reference_text)}, meta_thinking={_looks_like_meta_thinking(parsed.reference_text)})"
                    )
                    raw = _minimal_reference(problem)
                    parsed = ReferenceSolutionPayload(**raw)
                    local_schema_fallback = True
                else:
                    logging.info("[generate_reference_solution] Successfully generated reference solution")
            else:
                logging.warning("[generate_reference_solution] Generated reference is placeholder/unavailable")
                local_schema_fallback = True
        except ValidationError as e:
            logging.error(f"[generate_reference_solution] Validation error: {e}")
            parsed = ReferenceSolutionPayload(
                reference_text=f"Reference solution unavailable for: {problem}",
                key_assertions=[],
            )
            local_schema_fallback = True

        flags, run_meta = record_provider_call(
            state,
            provider,
            node_name="reference",
            fallback_flag="reference_schema_fallback",
            local_schema_fallback=local_schema_fallback,
            started_at=_started_at,
        )

        if complexity_label:
            run_meta.setdefault("node_stats", {}).setdefault("reference", {})["complexity"] = complexity_label
        # Record reference quality signals so the audit log shows when the
        # reference was degraded (e.g. meta-thinking fallback, placeholder).
        ref_text_for_signal = parsed.reference_text if hasattr(parsed, "reference_text") else ""
        run_meta.setdefault("node_stats", {}).setdefault("reference", {})["reference_is_degraded"] = (
            _is_degraded(ref_text_for_signal) if ref_text_for_signal else True
        )

        trace = state.get("trace", [])
        mode_label = "tool-calling agent" if use_tools else "standard"
        if local_schema_fallback:
            trace.append(f"generate_reference_solution ({mode_label}): reference generation failed (fallback)")
        else:
            trace.append(f"generate_reference_solution ({mode_label}): reference generated")

        existing_logs = list(state.get("tool_calls_log", []))
        new_tool_logs = []
        computation_outputs: list[str] = []
        for idx, tc in enumerate(agent_tool_calls):
            preview = tc.get("result_preview", "")
            new_tool_logs.append({
                "node": "generate_reference_solution",
                "step_id": None,
                "tool_name": tc.get("tool_name", ""),
                "call_index": idx + 1,
                "code": tc.get("code", ""),
                "result_preview": preview,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            if preview and not _is_failed_preview(preview):
                computation_outputs.append(f"[调用{idx + 1}] {preview}")

        hints = ""
        if computation_outputs:
            from stem_tutor.settings import hint_max_chars
            max_chars = hint_max_chars()
            combined = "参考解答阶段的 Python 计算结果（可直接使用，无需重复计算）：\n" + "\n".join(computation_outputs)
            if len(combined) > max_chars:
                combined = combined[:max_chars] + "\n...(truncated)"
            hints = combined

        return {
            "reference_solution": parsed.model_dump(),
            "reference_computation_hints": hints,
            "uncertainty_flags": flags,
            "trace": trace,
            "run_meta": run_meta,
            "tool_calls_log": existing_logs + new_tool_logs,
        }

    return generate_reference_solution_node
