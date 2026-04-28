from stem_tutor.domain.models import ProblemInput
from stem_tutor.graph.workflow import run_tutor_graph
from stem_tutor.providers.mock_provider import MockProvider


class LowConfidenceVerifyProvider(MockProvider):
    def verify_step(self, prompt: str) -> dict:
        return {
            "label": "unclear",
            "evidence": "Insufficient evidence to validate.",
            "confidence": 0.2,
            "violated_principles": ["schema_validation"],
        }


def test_high_uncertainty_triggers_manual_review_fail_reason():
    provider = LowConfidenceVerifyProvider()
    problem = ProblemInput(
        problem_id="policy-001",
        problem_text="Differentiate y = sin(x^2)",
        topic_tags=["derivative"],
    )
    raw_solution = "1) y' = cos(x^2)\n2) therefore done"

    out = run_tutor_graph(provider, problem, raw_solution)
    flags = set(out.get("uncertainty_flags", []))

    assert "too_many_low_confidence_steps" in flags
    assert "manual_review_required" in flags
    assert out.get("fail_reason") == "Verification uncertainty too high; manual review required."
    assert out["run_meta"]["failed"] is True