"""Tests for reference generation retry-with-progressive-hints (PR #27).

Covers:
- The retry loop fires when the strategy chain produces degraded output
- Hints are injected progressively (intensity escalates with attempts)
- Successful retry produces a non-degraded reference and sets observability flags
- All retries failing still falls back to the minimal reference and sets the
  `reference_retry_exhausted` flag
- The hints list contains the three escalation keywords (重要, CRITICAL, IRON RULE)
"""
from unittest.mock import patch

import pytest

from stem_tutor.domain.models import ProblemInput
from stem_tutor.nodes.generate_reference_solution import (
    _MAX_REFERENCE_ATTEMPTS,
    _REFERENCE_HINTS,
    make_generate_reference_solution_node,
)


def test_reference_hints_contain_all_three_escalation_keywords():
    """The hint list must contain 重要, CRITICAL, IRON RULE so LLM has
    three escalating prompts to draw from. Skip the first entry (no hint
    for the original prompt).
    """
    joined = " ".join(_REFERENCE_HINTS[1:])
    assert "**重要**" in joined, "attempt-2 hint should use 重要"
    assert "**CRITICAL**" in joined, "attempt-3 hint should use CRITICAL"
    assert "**IRON RULE**" in joined, "attempt-4 hint should use IRON RULE"
    assert _MAX_REFERENCE_ATTEMPTS == 4, "expected 4 total attempts (1 original + 3 retries)"


def _make_state():
    return {
        "problem_input": ProblemInput(
            problem_id="p1",
            problem_text="Compute the surface integral of xyz over S",
            topic_tags=[],
        ),
        "run_meta": {
            "node_stats": {
                "reference": {
                    "provider_calls": 0,
                    "fallback_calls": 0,
                    "reference_is_degraded": False,
                }
            }
        },
        "trace": [],
        "uncertainty_flags": [],
        "budget_enabled": True,
    }


class _MockProviderBase:
    """Base mock provider. Inherits LLMProvider interface bits the node touches."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self._last_call_meta = {"error_type": None, "retries": 0, "used_fallback": False}

    def get_last_call_meta(self) -> dict:
        return dict(self._last_call_meta)

    def set_last_call_meta(self, *, error_type=None, retries: int = 0, used_fallback: bool = False) -> None:
        self._last_call_meta = {"error_type": error_type, "retries": retries, "used_fallback": used_fallback}


class _DegradedThenValidProvider(_MockProviderBase):
    """Mock provider that returns degraded output on first call, valid on
    second. Records every call's hints argument.
    """

    def generate_reference_solution(self, problem_text, hints=None):
        self.calls.append(list(hints or []))
        if len(self.calls) == 1:
            return {
                "reference_text": "Unable to generate reference solution for: ...",
                "key_assertions": [],
            }
        return {
            "reference_text": (
                "Use Gauss theorem. The divergence is x^2+y^2+z^2. In spherical "
                "coordinates, the integrand is rho^2, the Jacobian is rho^2 sin(phi). "
                "The final answer is (2*pi/5) a^5."
            ),
            "key_assertions": [
                "divergence = x^2+y^2+z^2",
                "answer = (2*pi/5) a^5",
            ],
        }


class _AlwaysDegradedProvider(_MockProviderBase):
    def generate_reference_solution(self, problem_text, hints=None):
        self.calls.append(list(hints or []))
        return {
            "reference_text": "Unable to generate reference solution for: ...",
            "key_assertions": [],
        }


@pytest.fixture(autouse=True)
def _force_no_tool_calling(monkeypatch):
    """Force the old (non-agent) path so tests don't make real LLM calls."""
    monkeypatch.setattr(
        "stem_tutor.settings.is_tool_calling_enabled", lambda: False
    )


def test_retry_with_hints_succeeds_on_second_attempt():
    """First call: degraded. Second call (with hints): valid → retry succeeds."""
    provider = _DegradedThenValidProvider()
    state = _make_state()
    node = make_generate_reference_solution_node(provider)

    # First call to _is_degraded (on strategy-chain output) → True
    # Second call (on retry output) → False (valid)
    # Subsequent calls (validation etc.) → False
    is_degraded_values = iter([True, False, False, False, False])

    with patch(
        "stem_tutor.nodes.generate_reference_solution._is_degraded",
        side_effect=lambda _: next(is_degraded_values, False),
    ), patch(
        "stem_tutor.nodes.generate_reference_solution._minimal_reference",
        return_value={"reference_text": "minimal", "key_assertions": []},
    ):
        out = node(state)

    # Strategy chain `text_llm` (1 call) + retry (1 call) = 2 calls total
    assert len(provider.calls) == 2, (
        f"expected 2 calls (1 strategy-chain + 1 retry), got {len(provider.calls)}"
    )
    # First call is from the strategy chain with no hints
    assert provider.calls[0] == [], "strategy-chain call should have no hints"
    # Second call is from the retry loop with hints
    assert provider.calls[1] != [], "retry call should have hints"
    assert "**重要**" in provider.calls[1][0], (
        f"retry hint should include 重要, got: {provider.calls[1]}"
    )

    assert "reference_retry_with_hints" in out["uncertainty_flags"]
    assert "reference_retry_succeeded_at_attempt_1" in out["uncertainty_flags"]
    assert "reference_retry_exhausted" not in out["uncertainty_flags"]


def test_retry_exhausted_after_max_attempts():
    """All 4 calls return degraded → fallback to minimal + reference_retry_exhausted."""
    provider = _AlwaysDegradedProvider()
    state = _make_state()
    node = make_generate_reference_solution_node(provider)

    with patch(
        "stem_tutor.nodes.generate_reference_solution._is_degraded",
        return_value=True,
    ), patch(
        "stem_tutor.nodes.generate_reference_solution._minimal_reference",
        return_value={"reference_text": "minimal", "key_assertions": []},
    ):
        out = node(state)

    # Strategy chain `text_llm` (1 call) + 3 retries + 1 confidence-fallback = 5 calls total
    assert len(provider.calls) == 5, (
        f"expected 5 calls (1 strategy-chain + 3 retries + 1 confidence fallback), "
        f"got {len(provider.calls)}"
    )

    # First 4 calls: strategy chain (no hints) + 3 retries with escalating hints
    # Last call: confidence fallback (no hints)
    hint_intensities = [len(hints) for hints in provider.calls]
    assert hint_intensities == [0, 1, 2, 3, 0], (
        f"hint count should be [0, 1, 2, 3, 0], got {hint_intensities}"
    )

    keywords_per_call = [" ".join(hints) for hints in provider.calls]
    assert "**重要**" in keywords_per_call[1], "attempt 2 should use 重要"
    assert "**CRITICAL**" in keywords_per_call[2], "attempt 3 should use CRITICAL"
    assert "**IRON RULE**" in keywords_per_call[3], "attempt 4 should use IRON RULE"

    assert "reference_retry_exhausted" in out["uncertainty_flags"]
    assert "reference_retry_with_hints" not in out["uncertainty_flags"]


def test_no_retry_when_initial_call_succeeds():
    """If the strategy chain's first call produces a valid reference,
    the retry loop should not fire (zero additional LLM calls).
    """

    class _SingleValidProvider(_MockProviderBase):
        def generate_reference_solution(self, problem_text, hints=None):
            self.calls.append(list(hints or []))
            return {
                "reference_text": (
                    "This is a valid reference solution that is plenty long "
                    "enough to pass the quality checks. It contains actual math "
                    "and explanation, so it should not be flagged as degraded."
                ),
                "key_assertions": ["assertion 1", "assertion 2"],
            }

    provider = _SingleValidProvider()
    state = _make_state()
    node = make_generate_reference_solution_node(provider)

    with patch(
        "stem_tutor.nodes.generate_reference_solution._is_degraded",
        return_value=False,
    ):
        out = node(state)

    # Only the strategy chain's text_llm call should have fired
    assert len(provider.calls) == 1, (
        f"retry should not fire when strategy chain succeeds, "
        f"got {len(provider.calls)} calls (expected 1 from strategy chain only)"
    )
    assert "reference_retry_with_hints" not in out["uncertainty_flags"]
    assert "reference_retry_exhausted" not in out["uncertainty_flags"]
