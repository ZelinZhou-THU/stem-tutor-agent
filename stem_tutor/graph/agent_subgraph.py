"""LangGraph agent sub-graph with SymPy tool-calling for computation."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field as dc_field
from typing import Annotated, Any, Optional

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.graph.message import add_messages
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from typing_extensions import TypedDict

from stem_tutor.tools import get_tools

logger = logging.getLogger(__name__)

# Schema keys used by the router (early-stop) and by
# _looks_like_schema in generate_reference_solution.py.
# Keep these two consumers in sync.
SCHEMA_KEYS: tuple[str, ...] = (
    "reference_text",
    "label",
    "error_code",
    "steps",
    "review_problems",
    "feedback",
    "concise_summary",
)

DEFAULT_MAX_ITERATIONS = 2

_llm_cache: dict[str, tuple] = {}


def _get_or_create_llm(
    base_url: str,
    api_key: str,
    model_name: str,
    temperature: float,
    max_tokens: int,
    tools: list,
    request_timeout: int | None = None,
) -> tuple:
    cache_key = f"{base_url}|{model_name}|{temperature}|{max_tokens}|{request_timeout}"
    if cache_key in _llm_cache:
        return _llm_cache[cache_key]
    llm_kwargs = dict(
        base_url=base_url,
        api_key=api_key,
        model=model_name,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    if request_timeout is not None:
        llm_kwargs["request_timeout"] = request_timeout
    llm = ChatOpenAI(**llm_kwargs)
    llm_with_tools = llm.bind_tools(tools)
    entry = (llm, llm_with_tools)
    _llm_cache[cache_key] = entry
    if len(_llm_cache) > 20:
        oldest = next(iter(_llm_cache))
        del _llm_cache[oldest]
    return entry


@dataclass
class AgentResult:
    messages: list[BaseMessage]
    tool_calls: list[dict] = dc_field(default_factory=list)
    termination_reason: str = "completed"
    elapsed_seconds: float = 0.0


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


class AgentSubgraph:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model_name: str,
        system_prompt: str,
        max_tokens: int = 4000,
        temperature: float = 0.2,
        tools: list | None = None,
        tool_model_name: str | None = None,
        request_timeout: int | None = None,
    ):
        self.tools = tools or get_tools()
        self.system_prompt = system_prompt
        self.tool_model_name = tool_model_name
        self.api_key = api_key
        self.base_url = base_url
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.request_timeout = request_timeout

        self.llm, self.llm_with_tools = _get_or_create_llm(
            base_url, api_key, model_name, temperature, max_tokens, self.tools, request_timeout=request_timeout,
        )

        if tool_model_name and tool_model_name != model_name:
            self.tool_llm, self.tool_llm_with_tools = _get_or_create_llm(
                base_url, api_key, tool_model_name, 0.1, 4000, self.tools, request_timeout=request_timeout,
            )
            self.dual_model = True
            logger.info(f"[AgentSubgraph] Dual-model mode: tool={tool_model_name}, reasoning={model_name}")
        else:
            self.tool_llm = None
            self.tool_llm_with_tools = None
            self.dual_model = False

        self._graph = None
        self._graph_max_iterations = None

    def _extract_tool_calls(self, messages: list[BaseMessage]) -> list[dict]:
        tool_calls = []
        call_map: dict[str, dict] = {}
        for msg in messages:
            if isinstance(msg, AIMessage) and hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    call_id = tc.get("id", "")
                    args = tc.get("args", {})
                    entry = {
                        "tool_name": tc.get("name", ""),
                        "code": str(args.get("code", args.get("query", ""))),
                        "call_id": call_id,
                    }
                    tool_calls.append(entry)
                    if call_id:
                        call_map[call_id] = entry
            if isinstance(msg, ToolMessage):
                entry = call_map.get(msg.tool_call_id or "")
                if entry:
                    entry["result_preview"] = _extract_anchors(str(msg.content))
        return tool_calls

    def _agent_node(self, state: AgentState) -> dict:
        import time
        round_idx = sum(1 for m in state["messages"] if isinstance(m, AIMessage)) + 1
        llm = self.tool_llm_with_tools if self.dual_model else self.llm_with_tools
        start = time.time()
        response = llm.invoke(state["messages"])
        elapsed = time.time() - start
        model_used = getattr(response, "response_metadata", {}).get("model_name", "unknown")
        tc_count = len(response.tool_calls) if hasattr(response, "tool_calls") and response.tool_calls else 0
        logger.info(
            f"[AgentSubgraph] Round {round_idx} model={model_used} elapsed={elapsed:.2f}s "
            f"tool_calls={tc_count}"
        )
        return {"messages": [response]}

    def _make_router(self, max_tool_rounds: int):
        def router(state: AgentState) -> str:
            last_msg = state["messages"][-1] if state["messages"] else None
            if not isinstance(last_msg, AIMessage):
                return END

            # Early stop: only if the AI message itself is schema-valid JSON.
            # We previously checked for anchor substrings in the AI's prose,
            # which caused the agent to exit before producing the schema
            # required by the caller (e.g. {"reference_text": "..."}).
            if last_msg.content:
                parsed = parse_json_from_text(last_msg.content)
                if any(key in parsed for key in SCHEMA_KEYS):
                    logger.info("[AgentSubgraph] Detected schema-valid output, forcing END")
                    return END

            tool_rounds = sum(1 for m in state["messages"] if isinstance(m, ToolMessage))
            if tool_rounds >= max_tool_rounds:
                logger.info(
                    f"[AgentSubgraph] Reached max_tool_rounds={max_tool_rounds}, forcing END"
                )
                return END
            if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
                return "tools"
            return END
        return router

    def _get_or_build_graph(self, max_iterations: int):
        if self._graph is not None and self._graph_max_iterations == max_iterations:
            return self._graph
        self._graph = self._build(max_iterations)
        self._graph_max_iterations = max_iterations
        return self._graph

    def _build(self, max_tool_rounds: int):
        builder = StateGraph(AgentState)
        builder.add_node("agent", self._agent_node)
        builder.add_node("tools", ToolNode(self.tools))
        builder.add_edge(START, "agent")
        builder.add_conditional_edges("agent", self._make_router(max_tool_rounds))
        builder.add_edge("tools", "agent")
        return builder.compile()

    def invoke(
        self,
        user_message: str,
        max_iterations: int | None = None,
        max_duration_seconds: float | None = None,
    ) -> AgentResult:
        import time
        from stem_tutor.settings import agent_max_duration
        overall_start = time.time()
        iterations = max_iterations or DEFAULT_MAX_ITERATIONS
        deadline = agent_max_duration() if max_duration_seconds is None else max_duration_seconds

        graph = self._get_or_build_graph(iterations)

        if self.dual_model:
            return self._invoke_dual(user_message, iterations, deadline, graph)

        initial: AgentState = {
            "messages": [
                SystemMessage(content=self.system_prompt),
                HumanMessage(content=user_message),
            ]
        }
        result = graph.invoke(
            initial,
            config={"recursion_limit": iterations * 3 + 4},
        )
        messages = result.get("messages", [])
        tc = self._extract_tool_calls(messages)
        elapsed = time.time() - overall_start
        tool_rounds = sum(1 for m in messages if isinstance(m, ToolMessage))
        termination_reason = "completed"
        if elapsed >= deadline:
            termination_reason = "timeout"
        elif tool_rounds >= iterations:
            termination_reason = "max_rounds"
        # Only mark as success_anchor if a tool message actually emitted
        # the required anchors. (Was: matched AI prose, causing false
        # positives on meta-thinking replies.)
        elif any(
            ("FINAL_ANSWER=" in (m.content or "") and "KEY_RESULT=" in (m.content or ""))
            for m in messages
            if isinstance(m, ToolMessage)
        ):
            termination_reason = "success_anchor"
        logger.info(
            f"[AgentSubgraph] Completed in {elapsed:.2f}s, "
            f"termination={termination_reason}, tool_calls={len(tc)}"
        )
        return AgentResult(messages=messages, tool_calls=tc, termination_reason=termination_reason, elapsed_seconds=elapsed)

    def _invoke_dual(
        self,
        user_message: str,
        max_iterations: int,
        max_duration_seconds: float = 90.0,
        graph = None,
    ) -> AgentResult:
        import time
        overall_start = time.time()

        elapsed_check = time.time() - overall_start
        if elapsed_check >= max_duration_seconds:
            logger.warning(f"[AgentSubgraph] Deadline exceeded before phase1, elapsed={elapsed_check:.2f}s")
            return AgentResult(messages=[], tool_calls=[], termination_reason="timeout", elapsed_seconds=elapsed_check)

        tool_phase_prompt = (
            "你是一个数学计算助手。分析题目，编写 Python 代码调用 execute_python 工具完成所有必要的计算。\n"
            "请将所有计算写在一个代码块中一次性提交，用 print() 输出每个关键步骤的结果。\n"
            "在代码中加入自验证步骤（积分回验、代入检验等），确保计算正确。\n"
            "可用库：sympy、numpy、scipy、math。\n"
            "不要生成分步解答文本，只做计算。\n\n"
            "【严禁操作 - blacklist=v1】\n"
            "- 禁止使用 sp.integrate() 或 sympy.integrate()，会导致超时。用 scipy.integrate.quad() 做数值验证。\n"
            "- 代码总长度控制在 800 字符以内。\n\n"
            "【效率约束 - efficiency=v2】\n"
            "- 选择最短、最直接的验证路径，避免过度计算。\n"
            "- 优先使用数值方法（scipy.integrate.quad, numpy）而非符号积分。\n"
            "- 避免使用多种方法重复验证同一结果。\n\n"
            "【输出规范 - tool_prompt_policy=v1】\n"
            "- print() 输出优先使用 ASCII 字符，避免 Unicode 特殊字符。\n"
            "- 在代码末尾添加固定输出锚点：FINAL_ANSWER=、CHECK_PASS=true/false、KEY_RESULT=。\n"
            "- 仅使用允许库：sympy、numpy、scipy、math、fractions、json。"
        )

        initial: AgentState = {
            "messages": [
                SystemMessage(content=tool_phase_prompt),
                HumanMessage(content=user_message),
            ]
        }
        result = graph.invoke(
            initial,
            config={"recursion_limit": max_iterations * 3 + 4},
        )
        phase1_messages = result.get("messages", [])

        phase1_elapsed = time.time() - overall_start
        if phase1_elapsed >= max_duration_seconds:
            logger.warning(f"[AgentSubgraph] Deadline exceeded after phase1, elapsed={phase1_elapsed:.2f}s")
            tc = self._extract_tool_calls(phase1_messages)
            return AgentResult(messages=phase1_messages, tool_calls=tc, termination_reason="timeout", elapsed_seconds=phase1_elapsed)

        computation_summary = self._extract_computation_results(phase1_messages)

        phase2_messages = [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content=user_message),
        ]
        if computation_summary:
            phase2_messages.append(
                HumanMessage(
                    content=f"【以下是通过计算工具获得的精确结果，请直接基于这些结果组织你的解答，无需重新计算】\n"
                    f"{computation_summary}"
                )
            )

        phase2_response = self.llm.invoke(phase2_messages)
        all_messages = phase1_messages + phase2_messages[1:] + [phase2_response]
        tc = self._extract_tool_calls(phase1_messages)

        elapsed = time.time() - overall_start
        tool_rounds = sum(1 for m in phase1_messages if isinstance(m, ToolMessage))
        termination_reason = "completed"
        if elapsed >= max_duration_seconds:
            termination_reason = "timeout"
        elif tool_rounds >= max_iterations:
            termination_reason = "max_rounds"
        # Only call this "success_anchor" if the tool output actually contained
        # the required anchors. Previously this also fired when the AI quoted
        # anchors in its prose, which caused the caller to mistake a
        # meta-thinking reply for a completed reference solution.
        elif any(
            ("FINAL_ANSWER=" in (m.content or "") and "KEY_RESULT=" in (m.content or ""))
            for m in phase1_messages
            if isinstance(m, ToolMessage)
        ):
            termination_reason = "success_anchor"
        logger.info(
            f"[AgentSubgraph] Dual-mode completed in {elapsed:.2f}s, "
            f"termination={termination_reason}, tool_calls={len(tc)}"
        )
        return AgentResult(messages=all_messages, tool_calls=tc, termination_reason=termination_reason, elapsed_seconds=elapsed)

    def _extract_computation_results(self, messages: list[BaseMessage]) -> str:
        seen: set[str] = set()
        summaries: list[str] = []
        for msg in messages:
            if isinstance(msg, ToolMessage):
                compact = _extract_anchors(str(msg.content))
                if compact not in seen:
                    seen.add(compact)
                    summaries.append(f"- 工具({msg.name}): {compact}")
        if not summaries:
            return ""
        return "计算结果汇总：\n" + "\n".join(summaries)

    def get_last_ai_message(self, messages: list[BaseMessage]) -> Optional[AIMessage]:
        for msg in reversed(messages):
            if isinstance(msg, AIMessage) and msg.content:
                return msg
        return None


def _extract_anchors(text: str) -> str:
    from stem_tutor.settings import tool_result_max_chars
    max_chars = tool_result_max_chars()
    anchor_prefixes = ("FINAL_ANSWER=", "CHECK_PASS=", "KEY_RESULT=")
    anchors: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if any(stripped.startswith(p) for p in anchor_prefixes):
            anchors.append(stripped)
    if anchors:
        result = "\n".join(anchors)
        return result[:max_chars]
    return text[:max_chars]


def parse_json_from_text(text: str) -> dict[str, Any]:
    """Best-effort JSON object extraction from LLM output.

    Returns an empty dict on failure. Callers must explicitly check for the
    schema keys they expect. We deliberately do NOT return a sentinel like
    ``{"raw_text": text}`` because that anti-pattern made one caller
    (``_generate_via_agent``) treat arbitrary LLM prose as the user-visible
    reference solution.
    """
    if not text:
        return {}
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
        logger.warning(f"[parse_json_from_text] No JSON object found in agent output: {raw[:200]}")
        return {}

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
                for transform in (lambda c: c, _fix_json_control_chars, _fix_json_escapes):
                    try:
                        parsed = json.loads(transform(candidate))
                        if isinstance(parsed, dict):
                            return parsed
                    except json.JSONDecodeError:
                        continue
                logger.warning(f"[parse_json_from_text] All parse attempts failed: {raw[:200]}")
                return {}
    logger.warning(f"[parse_json_from_text] Unterminated JSON object: {raw[:200]}")
    return {}


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
            elif nxt == "u" and i + 5 < len(text) and all(
                c in "0123456789abcdefABCDEF" for c in text[i + 2 : i + 6]
            ):
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
