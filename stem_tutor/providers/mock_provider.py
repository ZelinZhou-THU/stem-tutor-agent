from __future__ import annotations

from typing import Any

from stem_tutor.domain.models import VerificationLabel
from stem_tutor.providers.base import LLMProvider
from stem_tutor.subjects.context import get_subject_context


class MockProvider(LLMProvider):
    """Deterministic mock outputs for fast local testing."""

    def __init__(self, model_group: str = "reasoning", model_name: str | None = None) -> None:
        super().__init__()
        self.model_group = model_group
        self.model_name = model_name or f"mock-{model_group}"

    def _get_mock_data(self) -> dict[str, Any]:
        try:
            from stem_tutor.prompts.templates import _current_subject_id
            ctx = get_subject_context(_current_subject_id())
            return {
                "reference_solution": ctx.mock_reference_solution,
                "review_problems": ctx.mock_review_problems,
            }
        except Exception:
            return {
                "reference_solution": {
                    "reference_text": "Differentiate using chain rule and simplify carefully.",
                    "key_assertions": [
                        "Apply derivative rules with inner derivative.",
                        "Keep signs and constants consistent.",
                    ],
                },
                "review_problems": [
                    {
                        "problem_text": "Compute d/dx [sin(x^2 + 1)].",
                        "related_weakness_code": "CHAIN_RULE_MISUSE",
                        "rationale": "Reinforces inner-function derivative tracking.",
                        "difficulty_label": "easy",
                    },
                    {
                        "problem_text": "Evaluate integral of 2x*cos(x^2) dx by substitution.",
                        "related_weakness_code": "SUBSTITUTION_MAPPING_MISMATCH",
                        "rationale": "Targets correct u and du mapping.",
                        "difficulty_label": "medium",
                    },
                ],
            }

    def health_check(self) -> tuple[bool, str]:
        return (True, "mock provider ready")

    def provider_info(self) -> dict[str, str]:
        return {
            "provider_name": "mock",
            "model_name": self.model_name,
        }

    def ocr_to_text(self, ocr_payload: str) -> dict[str, Any]:
        self.set_last_call_meta(error_type=None, retries=0, used_fallback=False)
        return {
            "text": "1) y' = cos(x^2)\n2) therefore done",
            "quality_score": 0.82 if ocr_payload else 0.4,
            "warnings": [] if ocr_payload else ["empty_ocr_payload"],
            "formula_format": "latex_like",
        }

    def invoke_structured(self, prompt: str, schema_hint: str, defaults: dict[str, Any]) -> dict[str, Any]:
        del prompt
        del schema_hint
        self.set_last_call_meta(error_type=None, retries=0, used_fallback=False)
        return dict(defaults)

    def generate_reference_solution(self, problem_text: str) -> dict[str, Any]:
        self.set_last_call_meta(error_type=None, retries=0, used_fallback=False)
        mock_data = self._get_mock_data()
        return dict(mock_data["reference_solution"])

    def parse_steps(self, raw_solution: str) -> list[dict[str, str]] | None:
        self.set_last_call_meta(error_type=None, retries=0, used_fallback=False)
        lines = [line.strip() for line in raw_solution.splitlines() if line.strip()]
        if not lines:
            return []
        return [{"text": line, "description": f"Step {i+1}"} for i, line in enumerate(lines)]

    def verify_step(self, prompt: str) -> dict[str, Any]:
        self.set_last_call_meta(error_type=None, retries=0, used_fallback=False)
        text = prompt.lower()
        if "therefore" in text and "=" not in text:
            return {
                "label": VerificationLabel.INCONSISTENT_OR_UNSUPPORTED.value,
                "evidence": "Claim is not supported by concrete transformation.",
                "confidence": 0.62,
                "violated_principles": ["insufficient_justification"],
            }
        if "u =" in text and "du" not in text:
            return {
                "label": VerificationLabel.INCORRECT_MATH.value,
                "evidence": "Substitution is introduced but differential mapping is missing.",
                "confidence": 0.78,
                "violated_principles": ["substitution_mapping"],
            }
        if len(text.strip()) < 8:
            return {
                "label": VerificationLabel.UNCLEAR.value,
                "evidence": "Step text is too short to validate.",
                "confidence": 0.55,
                "violated_principles": ["clarity"],
            }
        return {
            "label": VerificationLabel.CORRECT.value,
            "evidence": "Step is locally consistent with the reference pathway.",
            "confidence": 0.83,
            "violated_principles": [],
        }

    def diagnose_error(self, prompt: str) -> dict[str, Any]:
        self.set_last_call_meta(error_type=None, retries=0, used_fallback=False)
        lower = prompt.lower()
        if "substitution" in lower or "u-sub" in lower:
            return {
                "error_code": "SUBSTITUTION_MAPPING_MISMATCH",
                "root_cause_hypothesis": "Variable mapping between u and x was not carried through.",
                "supporting_evidence": "Differential or back-substitution step is missing.",
                "confidence": 0.79,
            }
        if "justification" in lower or "therefore" in lower:
            return {
                "error_code": "UNSUPPORTED_JUMP",
                "root_cause_hypothesis": "Student skipped an algebraic justification step.",
                "supporting_evidence": "Inference marker appears without intermediate equivalence.",
                "confidence": 0.72,
            }
        return {
            "error_code": "NOTATION_UNCLEAR",
            "root_cause_hypothesis": "Notation and symbol roles are unclear.",
            "supporting_evidence": "Expression lacks enough structure for stable interpretation.",
            "confidence": 0.65,
        }

    def generate_feedback(self, prompt: str) -> dict[str, Any]:
        self.set_last_call_meta(error_type=None, retries=0, used_fallback=False)
        return {
            "concise_summary": "Your setup is mostly right, but one key step is unsupported.",
            "next_action": "Rewrite the first incorrect step and explicitly show the missing transformation.",
            "caution_note": "When unsure, state the rule used before simplifying.",
        }

    def generate_review_problems(self, prompt: str) -> dict[str, Any]:
        self.set_last_call_meta(error_type=None, retries=0, used_fallback=False)
        mock_data = self._get_mock_data()
        return {"problems": mock_data["review_problems"]}
