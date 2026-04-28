from stem_tutor.domain.models import ProblemInput, SolutionStep, VerificationLabel, VerificationResult
from stem_tutor.nodes.diagnose_error import make_diagnose_error_node
from stem_tutor.nodes.verify_steps import make_verify_steps_node
from stem_tutor.providers.mock_provider import MockProvider


class BadVerifyProvider(MockProvider):
    def verify_step(self, prompt: str) -> dict:
        return {"oops": "invalid"}


class BadDiagnoseProvider(MockProvider):
    def diagnose_error(self, prompt: str) -> dict:
        return {
            "error_code": "HALLUCINATED_CODE",
            "root_cause_hypothesis": "unknown",
            "supporting_evidence": "unknown",
            "confidence": 0.9,
        }


def test_verify_schema_fallback_sets_uncertainty_flag():
    node = make_verify_steps_node(BadVerifyProvider())
    state = {
        "problem_input": ProblemInput(problem_id="p1", problem_text="Differentiate x^2", topic_tags=[]),
        "reference_solution": {"reference_text": "Use power rule", "key_assertions": ["d/dx x^2 = 2x"]},
        "normalized_steps": [
            SolutionStep(step_id="S1", raw_text="therefore done", normalized_text="therefore done")
        ],
        "trace": [],
        "uncertainty_flags": [],
    }
    out = node(state)
    assert "verify_schema_fallback" in out["uncertainty_flags"]
    assert out["verification_results"][0].label in {
        VerificationLabel.UNCLEAR,
        VerificationLabel.INCONSISTENT_OR_UNSUPPORTED,
    }


def test_diagnose_unknown_error_code_fallback_to_taxonomy():
    node = make_diagnose_error_node(BadDiagnoseProvider())
    state = {
        "normalized_steps": [
            SolutionStep(step_id="S1", raw_text="u=x^2", normalized_text="u=x^2")
        ],
        "verification_results": [
            VerificationResult(
                step_id="S1",
                label=VerificationLabel.INCORRECT_MATH,
                evidence="mapping issue",
                confidence=0.7,
            )
        ],
        "trace": [],
        "uncertainty_flags": [],
    }
    out = node(state)
    assert "diagnosis_unknown_error_code" in out["uncertainty_flags"]
    assert out["diagnosis_results"][0].error_code == "NOTATION_UNCLEAR"
