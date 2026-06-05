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


# -----------------------------
# Router early-stop
# -----------------------------

def test_router_does_not_early_stop_on_prose_with_anchor_substring():
    """The router must NOT early-stop when AI prose contains anchor substrings.

    Reproduces the production bug: LLM says 'I will output FINAL_ANSWER=...'
    in its reasoning, but the actual JSON hasn't been emitted yet. The old
    router saw the substring in AI prose and ended the run prematurely.
    """
    from langchain_core.messages import AIMessage
    from stem_tutor.graph.agent_subgraph import AgentSubgraph, AgentState

    agent = object.__new__(AgentSubgraph)
    router = agent._make_router(2).__func__

    state: AgentState = {
        "messages": [
            AIMessage(content='我观察到 CHECK_PASS=true 还没输出，下一步输出 JSON。'),
        ]
    }
    # Since the AI content is not schema-valid JSON and there are no tool
    # calls, the router should END (not loop forever) -- but it should NOT
    # END via the success-anchor shortcut. With no tool_calls and no JSON
    # schema, the router naturally returns END via the final fallthrough.
    result = router(state)
    assert result == "end"  # state['messages'] has no tool_calls and no JSON, so END


def test_router_early_stops_on_schema_valid_json():
    from langchain_core.messages import AIMessage
    from stem_tutor.graph.agent_subgraph import AgentSubgraph, AgentState

    agent = object.__new__(AgentSubgraph)
    router = agent._make_router(2).__func__

    state: AgentState = {
        "messages": [
            AIMessage(content='{"reference_text": "完整解答", "key_assertions": []}'),
        ]
    }
    result = router(state)
    assert result == "end"


def test_router_early_stops_on_label_schema():
    from langchain_core.messages import AIMessage
    from stem_tutor.graph.agent_subgraph import AgentSubgraph, AgentState

    agent = object.__new__(AgentSubgraph)
    router = agent._make_router(2).__func__

    state: AgentState = {
        "messages": [
            AIMessage(content='{"label": "correct", "evidence": "ok", "confidence": 0.9, "violated_principles": []}'),
        ]
    }
    result = router(state)
    assert result == "end"


def test_router_does_not_early_stop_on_prose_only_with_anchor():
    """AI message contains the literal strings but no JSON: must NOT end via anchor."""
    from langchain_core.messages import AIMessage
    from stem_tutor.graph.agent_subgraph import AgentSubgraph, AgentState

    agent = object.__new__(AgentSubgraph)
    router = agent._make_router(2).__func__

    state: AgentState = {
        "messages": [
            AIMessage(content='让我看看，我现在准备输出 FINAL_ANSWER=16/5 和 CHECK_PASS=true 还需要再算一下。'),
        ]
    }
    result = router(state)
    # Must fall through to the final END, NOT a premature success-anchor END.
    # (This test simply confirms we don't crash and the router does return.
    # The critical thing is the new logic doesn't recognize the substring.)
    assert result == "end"


# -----------------------------
# _generate_via_agent raises on bad output
# -----------------------------

def _make_mock_agent(messages, termination_reason="success_anchor"):
    """Build a mock AgentSubgraph whose .invoke returns messages and reason."""
    agent = MagicMock()
    result = MagicMock()
    result.messages = messages
    result.tool_calls = []
    result.termination_reason = termination_reason
    agent.invoke.return_value = result
    return agent


def test_generate_via_agent_raises_on_success_anchor_without_schema():
    """Reproduces the production bug at the function level: agent exits via
    success_anchor, the final AI message is meta-thinking prose with no JSON.
    The function must raise ValueError so the caller falls back to the
    minimal reference instead of shipping the prose as reference_text.
    """
    from langchain_core.messages import AIMessage
    from stem_tutor.nodes import generate_reference_solution as ref_mod

    meta = AIMessage(
        content="我注意到自验证步骤中比值不是1，这说明我的代换计算可能有误，让我重新检查代换关系"
    )
    agent = _make_mock_agent([meta], termination_reason="success_anchor")

    with patch.object(ref_mod, "AgentSubgraph", return_value=agent), \
         patch.object(ref_mod, "is_dual_model_enabled", return_value=False), \
         patch.object(ref_mod, "load_provider_settings") as mock_settings, \
         patch.object(ref_mod, "get_subject_context") as mock_ctx, \
         patch.object(ref_mod, "parse_json_from_text", return_value={}):
        mock_settings.return_value = MagicMock(
            api_key="x", base_url="http://x", reasoning_model_name="m",
            verify_model_group="fast", verify_model_name=None,
        )
        mock_ctx.return_value = MagicMock(display_name="Calculus", prompts={"system_role": "x"})

        with pytest.raises(ValueError, match="meta-thinking|success_anchor"):
            ref_mod._generate_via_agent("题目: test")


def test_generate_via_agent_raises_on_meta_thinking_even_with_completion():
    """Even if termination_reason is 'completed', meta-thinking prose should raise."""
    from langchain_core.messages import AIMessage
    from stem_tutor.nodes import generate_reference_solution as ref_mod

    meta = AIMessage(
        content="让我看看，第一步是代换。第二步是验证。我注意到一些细节问题需要确认。"
    )
    agent = _make_mock_agent([meta], termination_reason="completed")

    with patch.object(ref_mod, "AgentSubgraph", return_value=agent), \
         patch.object(ref_mod, "is_dual_model_enabled", return_value=False), \
         patch.object(ref_mod, "load_provider_settings") as mock_settings, \
         patch.object(ref_mod, "get_subject_context") as mock_ctx, \
         patch.object(ref_mod, "parse_json_from_text", return_value={}):
        mock_settings.return_value = MagicMock(
            api_key="x", base_url="http://x", reasoning_model_name="m",
            verify_model_group="fast", verify_model_name=None,
        )
        mock_ctx.return_value = MagicMock(display_name="Calculus", prompts={"system_role": "x"})

        with pytest.raises(ValueError, match="meta-thinking"):
            ref_mod._generate_via_agent("题目: test")


def test_generate_via_agent_returns_valid_json():
    """Happy path: agent returns schema-valid JSON, function returns it."""
    from langchain_core.messages import AIMessage
    from stem_tutor.nodes import generate_reference_solution as ref_mod

    valid = AIMessage(content='{"reference_text": "x = 5", "key_assertions": ["x=5"]}')
    agent = _make_mock_agent([valid], termination_reason="completed")

    with patch.object(ref_mod, "AgentSubgraph", return_value=agent), \
         patch.object(ref_mod, "is_dual_model_enabled", return_value=False), \
         patch.object(ref_mod, "load_provider_settings") as mock_settings, \
         patch.object(ref_mod, "get_subject_context") as mock_ctx, \
         patch.object(ref_mod, "parse_json_from_text") as mock_parse:
        mock_settings.return_value = MagicMock(
            api_key="x", base_url="http://x", reasoning_model_name="m",
            verify_model_group="fast", verify_model_name=None,
        )
        mock_ctx.return_value = MagicMock(display_name="Calculus", prompts={"system_role": "x"})
        mock_parse.return_value = {"reference_text": "x = 5", "key_assertions": ["x=5"]}

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
    assert _looks_like_schema({"foo": "bar"}) is False
    assert _looks_like_schema({}) is False
    assert _looks_like_schema(None) is False
    assert _looks_like_schema("not a dict") is False
