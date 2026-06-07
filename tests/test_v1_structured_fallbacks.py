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
    assert "关键判断" in text, (
        "verification_extra must include the key-judgment vs routine-calculation "
        "distinction for the step-skip rule"
    )

    assert "内部矛盾" in text or "步骤内部矛盾" in text, (
        "verification_extra must explicitly address the 'internal contradiction' "
        "case where input has typo but output is mathematically correct"
    )
    assert "示例" in text, (
        "verification_extra must include an example to anchor LLM behavior"
    )
    assert "R = zxy" in text or "zxy" in text, (
        "example must reference the canonical zxy vs 2xy case"
    )

    assert "等于 1" in text, (
        "step-skip rule must include the '= 1 constant factor' canonical case"
    )
    assert "∫sinφ" in text or "sinφ" in text, (
        "step-skip example must reference the sinφ integration case"
    )
    assert "积分上下限" in text, (
        "verification_extra must enumerate key-judgment categories "
        "(integral bounds, sign, absolute value, etc.)"
    )

    assert "工具结果优先" in text, (
        "verification_extra must include the tool-result priority rule "
        "to prevent LLM from overriding integrator outputs"
    )
    assert "execute_python" in text, (
        "tool-priority rule must reference execute_python"
    )
    assert "具体" in text and "实质错误" in text, (
        "verification_extra must require concrete error before flagging"
    )
    assert "模糊判断" in text, (
        "concrete-error rule must explicitly mention vague judgments as insufficient"
    )


def test_fallback_verification_extra_rule_order_ocr_and_skip_first():
    """Lenient rules (OCR tolerance, step-skip tolerance) must come BEFORE
    the strict cross-step coherence rules, so the LLM considers them first.
    Regression lock for PR #25: the rule order was the root cause of S3
    being repeatedly misjudged as incorrect_math.
    """
    prompts = _fallback_prompts()
    text = prompts.get("verification_extra", "")

    pos_skip = text.find("跳步")
    pos_ocr = text.find("OCR")
    pos_cross = text.find("跨步连贯性")
    pos_incorrect = text.find("incorrect_math")

    assert pos_skip != -1 and pos_ocr != -1, "must contain 跳步 and OCR"
    assert pos_cross != -1 and pos_incorrect != -1, "must contain 跨步连贯性 and incorrect_math"

    assert pos_skip < pos_cross, (
        f"rule 跳步 (pos {pos_skip}) must come before 跨步连贯性 (pos {pos_cross})"
    )
    assert pos_ocr < pos_cross, (
        f"rule OCR (pos {pos_ocr}) must come before 跨步连贯性 (pos {pos_cross})"
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

    for keyword in [
        "OCR", "字形", "跳步", "(a)", "(b)", "CORRECT",
        "内部矛盾", "示例", "zxy",
        "等于 1", "积分上下限",
        "工具结果优先", "execute_python", "具体", "实质错误", "模糊判断",
    ]:
        assert keyword in calculus_text, (
            f"calculus.yaml verification_extra missing keyword: {keyword}"
        )
        assert keyword in fallback_text, (
            f"fallback verification_extra missing keyword: {keyword}"
        )
