from stem_tutor.domain.models import VerificationLabel, VerificationResult, SolutionStep
from stem_tutor.nodes.verify_steps import (
    _default_label_step,
    _rule_based_fallback_verify,
    _outcome_to_verification_result,
    _rule_based_adjustment,
)


def _make_step(text, step_id="S1"):
    return SolutionStep(step_id=step_id, raw_text=text, normalized_text=text)


def test_default_label_math_step():
    step = _make_step("\\frac{3}{5} = 0.6")
    result = _default_label_step(step)
    assert result.label == VerificationLabel.UNCLEAR
    assert result.confidence == 0.25
    assert "verification_default_label" in result.violated_principles


def test_default_label_text_step():
    step = _make_step("this is a text step with no math")
    result = _default_label_step(step)
    assert result.label == VerificationLabel.CORRECT
    assert result.confidence == 0.3
    assert "verification_default_label" in result.violated_principles


def test_rule_based_fallback_no_match():
    step = _make_step("x = 3 + 5", step_id="S2")
    result = _rule_based_fallback_verify(step)
    assert result.step_id == "S2"
    assert result.confidence == 0.3
    assert "verification_budget_limited" in result.violated_principles


def test_never_zero_confidence():
    steps = [
        _make_step("hello world", "S1"),
        _make_step("x = 1 + 2 = 3", "S2"),
        _make_step("no equals here", "S3"),
    ]
    for s in steps:
        result = _default_label_step(s)
        assert result.confidence >= 0.25, f"Step {s.step_id} has confidence {result.confidence} < 0.25"


def test_outcome_min_confidence_025():
    from stem_tutor.graph.strategy import StrategyOutcome
    outcome = StrategyOutcome(None, "failed", 0.0, strategy_name="test")
    result = _outcome_to_verification_result(outcome, "S1")
    assert result.confidence == 0.25
    assert result.label == VerificationLabel.UNCLEAR


def test_fallback_adds_violated_principle():
    step = _make_step("some text", "S3")
    result = _rule_based_fallback_verify(step)
    assert "verification_budget_limited" in result.violated_principles


def test_default_label_step_principle():
    step = _make_step("calculation = 42", "S4")
    result = _default_label_step(step)
    assert "verification_default_label" in result.violated_principles
    assert result.step_id == "S4"
