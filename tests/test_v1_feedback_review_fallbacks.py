from stem_tutor.domain.models import ErrorDiagnosis, ProblemInput
from stem_tutor.nodes.generate_feedback import make_generate_feedback_node
from stem_tutor.nodes.generate_review_problems import make_generate_review_problems_node
from stem_tutor.providers.mock_provider import MockProvider


class BadFeedbackProvider(MockProvider):
    def generate_feedback(self, prompt: str) -> dict:
        return {"x": 1}


class BadReviewProvider(MockProvider):
    def generate_review_problems(self, prompt: str) -> dict:
        return {
            "problems": [
                {
                    "problem_text": "Example",
                    "related_weakness_code": "FAKE_CODE",
                    "rationale": "test",
                    "difficulty_label": "easy",
                }
            ]
        }


def test_feedback_schema_fallback_sets_flag():
    node = make_generate_feedback_node(BadFeedbackProvider())
    state = {
        "diagnosis_results": [
            ErrorDiagnosis(
                step_id="S1",
                error_code="UNSUPPORTED_JUMP",
                category="Reasoning Quality Issues",
                root_cause_hypothesis="Skipped justification",
                supporting_evidence="therefore without equivalence",
                confidence=0.7,
            )
        ],
        "trace": [],
        "uncertainty_flags": [],
    }
    out = node(state)
    assert "feedback_schema_fallback" in out["uncertainty_flags"]
    assert out["final_feedback"].next_action


def test_review_unknown_code_fallback_sets_flag():
    node = make_generate_review_problems_node(BadReviewProvider())
    state = {
        "problem_input": ProblemInput(
            problem_id="p1",
            problem_text="Differentiate x^2",
            topic_tags=["derivative"],
        ),
        "diagnosis_results": [
            ErrorDiagnosis(
                step_id="S1",
                error_code="UNSUPPORTED_JUMP",
                category="Reasoning Quality Issues",
                root_cause_hypothesis="Skipped justification",
                supporting_evidence="therefore without equivalence",
                confidence=0.7,
            )
        ],
        "trace": [],
        "uncertainty_flags": [],
    }
    out = node(state)
    assert "review_unknown_weakness_code" in out["uncertainty_flags"]
    assert out["review_problems"][0].related_weakness_code == "NOTATION_UNCLEAR"
