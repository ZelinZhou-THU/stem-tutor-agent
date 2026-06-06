"""Tests for the JSON schema enforcement in AgentSubgraph (PR fix/llm-json-schema-enforcement).

Covers:
- response_format is bound only to the answer-synthesizing LLM
- response_format is NOT bound to tool_llm / tool_llm_with_tools
- Router forces a tool round when schema appears without prior tool call
- Router allows END when schema appears after a tool round
- Router still allows END when max_tool_rounds is reached even without tools
"""

from unittest.mock import MagicMock, patch

import pytest


# -----------------------------
# response_format binding scope
# -----------------------------


def _make_factory():
    """Return a side_effect that creates distinct MagicMock objects so we can
    track which one was bound."""
    counter = {"n": 0}

    def factory(*args, **kwargs):
        counter["n"] += 1
        idx = counter["n"]
        llm = MagicMock(name=f"llm_{idx}")
        llm_w_tools = MagicMock(name=f"llm_w_tools_{idx}")
        llm.bind = MagicMock(name=f"llm_{idx}.bind", return_value=llm)
        llm_w_tools.bind = MagicMock(name=f"llm_w_tools_{idx}.bind", return_value=llm_w_tools)
        return llm, llm_w_tools

    return factory


def test_response_format_binds_llm_with_tools_in_single_model_mode():
    """In single-model mode, response_format binds to llm_with_tools (the
    LLM that _agent_node actually invokes)."""
    from stem_tutor.graph.agent_subgraph import AgentSubgraph

    mocks = []
    counter = {"n": 0}

    def track(*a, **kw):
        counter["n"] += 1
        llm_m = MagicMock()
        llm_w_t = MagicMock()
        llm_m.bind = MagicMock(return_value=llm_m)
        llm_w_t.bind = MagicMock(return_value=llm_w_t)
        mocks.append((llm_m, llm_w_t))
        return llm_m, llm_w_t

    with patch("stem_tutor.graph.agent_subgraph._get_or_create_llm") as mock_factory:
        mock_factory.side_effect = track
        AgentSubgraph(
            api_key="k", base_url="u", model_name="m",
            system_prompt="s", tool_model_name=None,
            response_format={"type": "json_object"},
        )
    (llm, llm_w_tools) = mocks[0]
    llm_w_tools.bind.assert_called_once_with(response_format={"type": "json_object"})


def test_response_format_binds_llm_not_tool_llm_in_dual_model_mode():
    """In dual-model mode, response_format binds to llm (used in
    _invoke_dual Phase 2) but NOT to tool_llm_with_tools (used for
    tool dispatch in Phase 1)."""
    from stem_tutor.graph.agent_subgraph import AgentSubgraph

    mocks = []
    counter = {"n": 0}

    def track(*a, **kw):
        counter["n"] += 1
        llm_m = MagicMock()
        llm_w_t = MagicMock()
        llm_m.bind = MagicMock(return_value=llm_m)
        llm_w_t.bind = MagicMock(return_value=llm_w_t)
        mocks.append((llm_m, llm_w_t))
        return llm_m, llm_w_t

    with patch("stem_tutor.graph.agent_subgraph._get_or_create_llm") as mock_factory:
        mock_factory.side_effect = track
        AgentSubgraph(
            api_key="k", base_url="u", model_name="reasoning",
            system_prompt="s", tool_model_name="fast",
            response_format={"type": "json_object"},
        )

    # First call returned (llm, llm_with_tools); second returned (tool_llm, tool_llm_with_tools)
    (llm, llm_w_tools), (tool_llm, tool_llm_w_tools) = mocks

    # llm should be bound (it's the answer-synthesizing LLM in dual mode)
    llm.bind.assert_called_once_with(response_format={"type": "json_object"})

    # llm_with_tools is NOT used in dual mode (tool_llm_with_tools is); skip
    # tool_llm and tool_llm_with_tools should NOT be bound
    tool_llm.bind.assert_not_called()
    tool_llm_w_tools.bind.assert_not_called()


def test_no_response_format_leaves_llms_untouched():
    """When response_format is None, no LLM is bound."""
    from stem_tutor.graph.agent_subgraph import AgentSubgraph

    mocks = []
    counter = {"n": 0}

    def track(*a, **kw):
        counter["n"] += 1
        llm_m = MagicMock()
        llm_w_t = MagicMock()
        llm_m.bind = MagicMock(return_value=llm_m)
        llm_w_t.bind = MagicMock(return_value=llm_w_t)
        mocks.append((llm_m, llm_w_t))
        return llm_m, llm_w_t

    with patch("stem_tutor.graph.agent_subgraph._get_or_create_llm") as mock_factory:
        mock_factory.side_effect = track
        AgentSubgraph(
            api_key="k", base_url="u", model_name="m", system_prompt="s",
        )

    (llm, llm_w_tools) = mocks[0]
    llm.bind.assert_not_called()
    llm_w_tools.bind.assert_not_called()


def test_response_format_bind_failure_does_not_raise():
    """If bind() raises (e.g. provider rejects), the constructor should
    log a warning and continue with the original LLM."""
    from stem_tutor.graph.agent_subgraph import AgentSubgraph

    mocks = []

    def track(*a, **kw):
        llm_m = MagicMock()
        llm_w_t = MagicMock()
        llm_m.bind = MagicMock(side_effect=ValueError("provider rejected"))
        llm_w_t.bind = MagicMock(side_effect=ValueError("provider rejected"))
        mocks.append((llm_m, llm_w_t))
        return llm_m, llm_w_t

    with patch("stem_tutor.graph.agent_subgraph._get_or_create_llm") as mock_factory:
        mock_factory.side_effect = track
        # Should not raise
        sub = AgentSubgraph(
            api_key="k", base_url="u", model_name="m",
            system_prompt="s",
            response_format={"type": "json_object"},
        )
    # Original (un-bound) llm_with_tools should still be assigned
    assert sub.llm_with_tools is mocks[0][1]


# -----------------------------
# Router early-stop logic
# -----------------------------


def _make_subgraph():
    """Build an AgentSubgraph instance without running __init__."""
    from stem_tutor.graph.agent_subgraph import AgentSubgraph
    return AgentSubgraph.__new__(AgentSubgraph)


def test_router_allows_end_after_tool_call_with_schema():
    """After a tool round, if the AI emits schema, router returns END."""
    from langchain_core.messages import AIMessage, ToolMessage
    from stem_tutor.graph.agent_subgraph import AgentState

    sub = _make_subgraph()
    router = sub._make_router(max_tool_rounds=2)

    state: AgentState = {
        "messages": [
            AIMessage(content='{"reference_text": "x", "key_assertions": []}'),
            ToolMessage(
                content="FINAL_ANSWER=0\nKEY_RESULT=0",
                tool_call_id="t1",
                name="execute_python",
            ),
            AIMessage(content='{"reference_text": "verified", "key_assertions": ["k"]}'),
        ]
    }
    assert router(state) == "__end__"


def test_router_forces_tool_when_schema_appears_without_tool_round():
    """If first AI message is schema without any tool call, force a tool round."""
    from langchain_core.messages import AIMessage
    from stem_tutor.graph.agent_subgraph import AgentState

    sub = _make_subgraph()
    router = sub._make_router(max_tool_rounds=2)

    state: AgentState = {
        "messages": [
            AIMessage(content='{"reference_text": "unverified answer", "key_assertions": []}'),
        ]
    }
    assert router(state) == "tools"


def test_router_allows_end_when_max_tool_rounds_reached_without_tools():
    """If LLM insists on emitting schema without tools and max_tool_rounds=0,
    router falls through to END (avoid infinite loop)."""
    from langchain_core.messages import AIMessage
    from stem_tutor.graph.agent_subgraph import AgentState

    sub = _make_subgraph()
    # max_tool_rounds=0 means no tool calls allowed
    router = sub._make_router(max_tool_rounds=0)

    state: AgentState = {
        "messages": [
            AIMessage(content='{"reference_text": "answer", "key_assertions": []}'),
        ]
    }
    # tool_rounds (0) >= max_tool_rounds (0) is True, so the schema branch
    # accepts END immediately
    assert router(state) == "__end__"


def test_router_returns_end_when_no_schema_no_tool_calls():
    """If AI emits prose without schema and no tool calls, END (existing behavior)."""
    from langchain_core.messages import AIMessage
    from stem_tutor.graph.agent_subgraph import AgentState

    sub = _make_subgraph()
    router = sub._make_router(max_tool_rounds=2)

    state: AgentState = {
        "messages": [AIMessage(content="Let me think about this...")]
    }
    assert router(state) == "__end__"


def test_router_routes_to_tools_when_tool_calls_present():
    """If the AI message contains tool_calls (not just schema), route to tools."""
    from langchain_core.messages import AIMessage
    from stem_tutor.graph.agent_subgraph import AgentState

    sub = _make_subgraph()
    router = sub._make_router(max_tool_rounds=2)

    state: AgentState = {
        "messages": [
            AIMessage(
                content="Let me verify by running some code.",
                tool_calls=[{"id": "t1", "name": "execute_python", "args": {"code": "print(1+1)"}}],
            ),
        ]
    }
    assert router(state) == "tools"
