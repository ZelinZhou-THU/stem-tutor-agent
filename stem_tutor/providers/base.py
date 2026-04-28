from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError


ModelT = TypeVar("ModelT", bound=BaseModel)


class LLMProvider(ABC):
    def __init__(self) -> None:
        self._last_call_meta: dict[str, Any] = {
            "error_type": None,
            "retries": 0,
            "used_fallback": False,
        }

    def health_check(self) -> tuple[bool, str]:
        return (True, "not implemented")

    def provider_info(self) -> dict[str, str]:
        return {
            "provider_name": self.__class__.__name__,
            "model_name": "unknown",
        }

    def invoke_structured(self, prompt: str, schema_hint: str, defaults: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def ocr_to_text(self, ocr_payload: str) -> dict[str, Any]:
        raise NotImplementedError

    def set_last_call_meta(self, *, error_type: str | None, retries: int, used_fallback: bool) -> None:
        self._last_call_meta = {
            "error_type": error_type,
            "retries": retries,
            "used_fallback": used_fallback,
        }

    def get_last_call_meta(self) -> dict[str, Any]:
        return dict(self._last_call_meta)

    def validate_or_fallback(
        self,
        raw: dict[str, Any],
        model_cls: type[ModelT],
        fallback_payload: ModelT,
    ) -> dict[str, Any]:
        try:
            payload = model_cls(**raw)
            return payload.model_dump()
        except ValidationError:
            meta = self.get_last_call_meta()
            self.set_last_call_meta(
                error_type=meta.get("error_type") or "schema_validation",
                retries=int(meta.get("retries", 0)),
                used_fallback=True,
            )
            return fallback_payload.model_dump()

    @abstractmethod
    def generate_reference_solution(self, problem_text: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def parse_steps(self, raw_solution: str) -> list[dict[str, str]] | None:
        raise NotImplementedError

    @abstractmethod
    def verify_step(self, prompt: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def diagnose_error(self, prompt: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def generate_feedback(self, prompt: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def generate_review_problems(self, prompt: str) -> dict[str, Any]:
        raise NotImplementedError
