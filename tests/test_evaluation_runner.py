from pathlib import Path

from stem_tutor.evaluation.runner import evaluate_cases
from stem_tutor.providers.mock_provider import MockProvider


def test_evaluation_runner_outputs_summary():
    cases = Path("fixtures/eval_cases.json")
    result = evaluate_cases(MockProvider(), cases)

    assert result["num_cases"] >= 8
    assert "avg_verification_accuracy" in result
    assert "avg_diagnosis_hit" in result
    assert "avg_error_step_recall" in result
    assert "avg_taxonomy_category_hit" in result
    assert "avg_first_error_hit" in result
    assert "avg_feedback_proxy" in result
    assert "avg_review_relevance_proxy" in result
    assert "avg_low_conf_trigger_rate" in result
    assert "avg_real_provider_failure_rate" in result
    assert "avg_uncertainty_flags" in result
    assert "real_failure_reasons" in result
    assert "real_provider_error_types" in result
    assert isinstance(result["real_failure_reasons"], dict)
    assert isinstance(result["real_provider_error_types"], dict)
    assert len(result["rows"]) == result["num_cases"]


def test_evaluation_runner_supports_baseline_modes():
    cases = Path("fixtures/eval_cases.json")
    result = evaluate_cases(MockProvider(model_group="baseline", model_name="mock-GLM-5-Turbo"), cases, mode="baseline_glm5")

    assert result["num_cases"] >= 8
    assert all(row["mode"] == "baseline_glm5" for row in result["rows"])
