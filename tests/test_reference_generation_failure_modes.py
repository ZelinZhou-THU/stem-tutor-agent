"""Tests for the reference-solution quality hardening (PR 1).

Covers:
- Router no longer early-stops on prose containing anchor substrings.
- parse_json_from_text no longer returns the {"raw_text": ...} sentinel.
- _looks_like_meta_thinking detects meta-cognitive LLM output.
- _is_degraded consults _looks_like_meta_thinking.
- _generate_via_agent raises when agent produces no schema.
- _generate_via_agent raises when output looks like meta-thinking.
"""

import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


# -----------------------------
# parse_json_from_text
# -----------------------------

def test_parse_json_from_text_no_braces_returns_empty_dict():
    from stem_tutor.graph.agent_subgraph import parse_json_from_text
    assert parse_json_from_text("我注意到自验证步骤中比值不是1") == {}


def test_parse_json_from_text_empty_input_returns_empty_dict():
    from stem_tutor.graph.agent_subgraph import parse_json_from_text
    assert parse_json_from_text("") == {}


def test_parse_json_from_text_malformed_json_returns_empty_dict():
    from stem_tutor.graph.agent_subgraph import parse_json_from_text
    assert parse_json_from_text("{not valid json}") == {}


def test_parse_json_from_text_truncated_returns_empty_dict():
    from stem_tutor.graph.agent_subgraph import parse_json_from_text
    assert parse_json_from_text('{"reference_text": "incomplete') == {}


def test_parse_json_from_text_valid_returns_dict():
    from stem_tutor.graph.agent_subgraph import parse_json_from_text
    out = parse_json_from_text('{"reference_text": "foo", "key_assertions": []}')
    assert out == {"reference_text": "foo", "key_assertions": []}


def test_parse_json_from_text_markdown_fenced():
    from stem_tutor.graph.agent_subgraph import parse_json_from_text
    out = parse_json_from_text('```json\n{"reference_text": "foo"}\n```')
    assert out == {"reference_text": "foo"}


def test_parse_json_from_text_no_raw_text_sentinel():
    """The {'raw_text': text} sentinel that caused the production bug is gone."""
    from stem_tutor.graph.agent_subgraph import parse_json_from_text
    out = parse_json_from_text("我注意到自验证步骤中比值不是1")
    assert "raw_text" not in out


# -----------------------------
# _looks_like_meta_thinking
# -----------------------------

@pytest.mark.parametrize("text,expected", [
    ("我注意到自验证步骤中比值不是1...", True),
    ("让我重新检查代换关系", True),
    ("我看到这个结果不太对", True),
    ("I notice the ratio is not 1", True),
    ("Let me verify this calculation", True),
    ("It seems there's an issue", True),
    ("", True),
    ("$$x^2 + 2x + 1 = (x+1)^2$$ and \\boxed{4}", False),
    ("计算结果为 16/5 = 3.2", False),
    ("some long text but with no math at all " * 20, True),  # >100 chars, no math
    ("short", False),  # <50 chars, length gate handled by _is_degraded
])
def test_looks_like_meta_thinking(text, expected):
    from stem_tutor.nodes.generate_reference_solution import _looks_like_meta_thinking
    assert _looks_like_meta_thinking(text) is expected


# -----------------------------
# _is_degraded
# -----------------------------

def test_is_degraded_detects_meta_thinking():
    from stem_tutor.nodes.generate_reference_solution import _is_degraded
    # >50 chars, meta-thinking prefix -> degraded
    assert _is_degraded("我注意到自验证步骤中比值不是1，这说明我的代换计算可能有误") is True


def test_is_degraded_detects_long_no_math():
    from stem_tutor.nodes.generate_reference_solution import _is_degraded
    long_text = "This is a long text with no mathematical symbols at all. " * 5
    assert _is_degraded(long_text) is True


def test_is_degraded_passes_legitimate_reference():
    from stem_tutor.nodes.generate_reference_solution import _is_degraded
    legit = "$\\int_0^1 \\frac{dx}{\\sqrt{1-x^{1/3}}} = 3B(3, 1/2) = \\frac{16}{5}$"
    assert _is_degraded(legit) is False


def test_is_degraded_short_text():
    from stem_tutor.nodes.generate_reference_solution import _is_degraded
    assert _is_degraded("short") is True
    assert _is_degraded("") is True


def test_is_degraded_markers():
    from stem_tutor.nodes.generate_reference_solution import _is_degraded
    assert _is_degraded("计算超时了，请稍后重试 a long enough string here to pass length") is True
    assert _is_degraded("Unable to generate a long enough fallback string here ok") is True
    # Case-insensitive: lowercase marker must match a capitalised occurrence
    assert _is_degraded("UNABLE TO GENERATE a long enough fallback string here ok") is True


# -----------------------------
# Router early-stop
# -----------------------------

def _build_agent():
    """Create a bare AgentSubgraph without invoking __init__ (skips LLM setup)."""
    from stem_tutor.graph.agent_subgraph import AgentSubgraph
    return object.__new__(AgentSubgraph)


def test_router_does_not_early_stop_on_prose_with_anchor_substring():
    """The router must NOT early-stop when AI prose contains anchor substrings.

    Reproduces the production bug: LLM says 'I will output FINAL_ANSWER=...'
    in its reasoning, but the actual JSON hasn't been emitted yet. The old
    router saw the substring in AI prose and ended the run prematurely.

    To distinguish buggy (early-END on anchor substring) vs fixed (proceeds
    to tool_calls) code, we attach a fake tool_call to the AI message and
    assert the router routes to "tools" -- which would only happen if the
    schema-key check (and the now-removed anchor check) both do not fire.
    """
    from langchain_core.messages import AIMessage
    from langgraph.graph import END
    from stem_tutor.graph.agent_subgraph import AgentState

    agent = _build_agent()
    router = agent._make_router(2)

    ai = AIMessage(content='我观察到 CHECK_PASS=true 还没输出，下一步输出 JSON。')
    # Attach a tool_call so the router, if it reaches the tool_calls check,
    # routes to "tools". This proves the schema-key check (and the legacy
    # anchor-substring check) both do not short-circuit to END.
    ai.tool_calls = [{"id": "c1", "name": "execute_python", "args": {"code": "print(1)"}}]

    state: AgentState = {"messages": [ai]}
    result = router(state)
    assert result == "tools", (
        f"router early-stopped (got {result!r}); AI prose with anchor substring "
        f"and tool_calls present must route to 'tools'"
    )
    assert result != END


def test_router_early_stops_on_schema_valid_json():
    from langchain_core.messages import AIMessage
    from langgraph.graph import END
    from stem_tutor.graph.agent_subgraph import AgentState

    agent = _build_agent()
    router = agent._make_router(2)

    state: AgentState = {
        "messages": [
            AIMessage(content='{"reference_text": "完整解答", "key_assertions": []}'),
        ]
    }
    result = router(state)
    assert result == END


def test_router_early_stops_on_label_schema():
    from langchain_core.messages import AIMessage
    from langgraph.graph import END
    from stem_tutor.graph.agent_subgraph import AgentState

    agent = _build_agent()
    router = agent._make_router(2)

    state: AgentState = {
        "messages": [
            AIMessage(content='{"label": "correct", "evidence": "ok", "confidence": 0.9, "violated_principles": []}'),
        ]
    }
    result = router(state)
    assert result == END


def test_router_does_not_early_stop_on_prose_only_with_anchor():
    """AI message contains the literal anchor strings but no JSON: must NOT end via anchor.

    We attach a tool_call and assert the router routes to "tools", proving
    the schema-key check does not short-circuit on anchor substrings.
    """
    from langchain_core.messages import AIMessage
    from langgraph.graph import END
    from stem_tutor.graph.agent_subgraph import AgentState

    agent = _build_agent()
    router = agent._make_router(2)

    ai = AIMessage(content='让我看看，我现在准备输出 FINAL_ANSWER=16/5 和 CHECK_PASS=true 还需要再算一下。')
    ai.tool_calls = [{"id": "c1", "name": "execute_python", "args": {"code": "print(1)"}}]

    state: AgentState = {"messages": [ai]}
    result = router(state)
    assert result == "tools", (
        f"router short-circuited (got {result!r}); anchor substring in prose "
        f"must not trigger the schema-key early-stop"
    )
    assert result != END


# -----------------------------
# _generate_via_agent raises on bad output
# -----------------------------

def _make_mock_agent(last_ai_message, termination_reason="success_anchor"):
    """Build a mock AgentSubgraph whose .invoke returns messages and reason.

    `_generate_via_agent` calls both `result.messages` and
    `agent.get_last_ai_message(messages)`, so we wire both up.
    """
    agent = MagicMock()
    result = MagicMock()
    result.messages = [last_ai_message]
    result.tool_calls = []
    result.termination_reason = termination_reason
    agent.invoke.return_value = result
    agent.get_last_ai_message.return_value = last_ai_message
    return agent


def test_generate_via_agent_raises_on_success_anchor_without_schema():
    """Reproduces the production bug at the function level: agent exits via
    success_anchor, the final AI message is meta-thinking prose with no JSON.
    The function must raise ValueError so the caller falls back to the
    minimal reference instead of shipping the prose as reference_text.
    """
    from langchain_core.messages import AIMessage

    meta = AIMessage(
        content="我注意到自验证步骤中比值不是1，这说明我的代换计算可能有误，让我重新检查代换关系"
    )
    agent = _make_mock_agent(meta, termination_reason="success_anchor")

    # Patch the source modules: _generate_via_agent uses
    # `from stem_tutor.graph.agent_subgraph import AgentSubgraph, parse_json_from_text`
    # inside the function body, so module-attribute patches on the consumer
    # module are no-ops.
    with patch("stem_tutor.graph.agent_subgraph.AgentSubgraph", return_value=agent), \
         patch("stem_tutor.settings.is_dual_model_enabled", return_value=False), \
         patch("stem_tutor.settings.load_provider_settings") as mock_settings, \
         patch("stem_tutor.settings.reference_max_tool_rounds", return_value=2), \
         patch("stem_tutor.subjects.context.get_subject_context") as mock_ctx, \
         patch("stem_tutor.graph.agent_subgraph.parse_json_from_text", return_value={}):
        mock_settings.return_value = MagicMock(
            api_key="x", base_url="http://x", reasoning_model_name="m",
            verify_model_group="fast", verify_model_name=None,
        )
        mock_ctx.return_value = MagicMock(display_name="Calculus", prompts={"system_role": "x"})

        with pytest.raises(ValueError, match="meta-thinking|success_anchor"):
            from stem_tutor.nodes import generate_reference_solution as ref_mod
            ref_mod._generate_via_agent("题目: test")


def test_generate_via_agent_raises_on_meta_thinking_even_with_completion():
    """Even if termination_reason is 'completed', meta-thinking prose should raise."""
    from langchain_core.messages import AIMessage

    meta = AIMessage(
        content="让我看看，第一步是代换。第二步是验证。我注意到一些细节问题需要确认。"
    )
    agent = _make_mock_agent(meta, termination_reason="completed")

    with patch("stem_tutor.graph.agent_subgraph.AgentSubgraph", return_value=agent), \
         patch("stem_tutor.settings.is_dual_model_enabled", return_value=False), \
         patch("stem_tutor.settings.load_provider_settings") as mock_settings, \
         patch("stem_tutor.settings.reference_max_tool_rounds", return_value=2), \
         patch("stem_tutor.subjects.context.get_subject_context") as mock_ctx, \
         patch("stem_tutor.graph.agent_subgraph.parse_json_from_text", return_value={}):
        mock_settings.return_value = MagicMock(
            api_key="x", base_url="http://x", reasoning_model_name="m",
            verify_model_group="fast", verify_model_name=None,
        )
        mock_ctx.return_value = MagicMock(display_name="Calculus", prompts={"system_role": "x"})

        with pytest.raises(ValueError, match="meta-thinking"):
            from stem_tutor.nodes import generate_reference_solution as ref_mod
            ref_mod._generate_via_agent("题目: test")


def test_generate_via_agent_returns_valid_json():
    """Happy path: agent returns schema-valid JSON, function returns it."""
    from langchain_core.messages import AIMessage

    valid = AIMessage(content='{"reference_text": "x = 5", "key_assertions": ["x=5"]}')
    agent = _make_mock_agent(valid, termination_reason="completed")

    with patch("stem_tutor.graph.agent_subgraph.AgentSubgraph", return_value=agent), \
         patch("stem_tutor.settings.is_dual_model_enabled", return_value=False), \
         patch("stem_tutor.settings.load_provider_settings") as mock_settings, \
         patch("stem_tutor.settings.reference_max_tool_rounds", return_value=2), \
         patch("stem_tutor.subjects.context.get_subject_context") as mock_ctx, \
         patch("stem_tutor.graph.agent_subgraph.parse_json_from_text") as mock_parse:
        mock_settings.return_value = MagicMock(
            api_key="x", base_url="http://x", reasoning_model_name="m",
            verify_model_group="fast", verify_model_name=None,
        )
        mock_ctx.return_value = MagicMock(display_name="Calculus", prompts={"system_role": "x"})
        mock_parse.return_value = {"reference_text": "x = 5", "key_assertions": ["x=5"]}

        from stem_tutor.nodes import generate_reference_solution as ref_mod
        raw, tools = ref_mod._generate_via_agent("题目: test", subject_id="calculus")
        assert raw["reference_text"] == "x = 5"
        assert raw["key_assertions"] == ["x=5"]


# -----------------------------
# _looks_like_schema helper
# -----------------------------

def test_looks_like_schema():
    from stem_tutor.nodes.generate_reference_solution import _looks_like_schema
    assert _looks_like_schema({"reference_text": "x"}) is True
    assert _looks_like_schema({"label": "correct"}) is True
    assert _looks_like_schema({"error_code": "X"}) is True
    assert _looks_like_schema({"review_problems": []}) is True
    assert _looks_like_schema({"steps": []}) is True
    assert _looks_like_schema({"concise_summary": "ok"}) is True
    assert _looks_like_schema({"feedback": "ok"}) is True
    assert _looks_like_schema({"foo": "bar"}) is False
    assert _looks_like_schema({}) is False
    assert _looks_like_schema(None) is False
    assert _looks_like_schema("not a dict") is False
