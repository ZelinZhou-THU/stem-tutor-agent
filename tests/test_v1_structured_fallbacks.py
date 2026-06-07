from stem_tutor.domain.models import ProblemInput, SolutionStep, VerificationLabel, VerificationResult
from stem_tutor.nodes.diagnose_error import make_diagnose_error_node
from stem_tutor.nodes.verify_steps import make_verify_steps_node
from stem_tutor.prompts.templates import _fallback_prompts
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


# =============================================================================
# Tests for verification_extra prompt rules (OCR tolerance + step-skip tolerance)
# =============================================================================


def test_fallback_verification_extra_includes_ocr_and_skip_rules():
    """The fallback verification_extra prompt must include the OCR-tolerance
    and step-skip-tolerance rules, with both condition (a) and (b) coverage.
    Regression lock: future edits to _fallback_prompts() should not silently
    drop these rules.
    """
    prompts = _fallback_prompts()
    text = prompts.get("verification_extra", "")

    assert "OCR" in text, "verification_extra must mention OCR"
    assert "字形" in text, "verification_extra must mention 字形 (shape) errors"
    assert "z/2" in text or "z ↔ 2" in text or "z 与 2" in text, (
        "verification_extra must enumerate common OCR confusions (e.g. z/2)"
    )

    assert "跳步" in text, "verification_extra must mention 跳步 (step-skipping)"
    assert "(a)" in text and "(b)" in text, (
        "verification_extra must include both conditions (a) and (b)"
    )
    assert "CORRECT" in text, (
        "verification_extra must reference final-answer CORRECT status"
    )
    assert "唯一" in text or "关键判断" in text, (
        "verification_extra must include guard clause to prevent over-relaxation"
    )


def test_calculus_verification_extra_matches_fallback():
    """The calculus subject's verification_extra must contain the same
    OCR-tolerance and step-skip-tolerance keywords as the fallback.
    Prevents drift between fallback (7 subjects) and calculus (1 subject).
    """
    import yaml
    from pathlib import Path

    prompts = _fallback_prompts()
    fallback_text = prompts["verification_extra"]

    config_path = Path(__file__).resolve().parent.parent / "stem_tutor" / "subjects" / "calculus.yaml"
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    calculus_text = config.get("prompts", {}).get("verification_extra", "")

    for keyword in ["OCR", "字形", "跳步", "(a)", "(b)", "CORRECT"]:
        assert keyword in calculus_text, (
            f"calculus.yaml verification_extra missing keyword: {keyword}"
        )
        assert keyword in fallback_text, (
            f"fallback verification_extra missing keyword: {keyword}"
        )
