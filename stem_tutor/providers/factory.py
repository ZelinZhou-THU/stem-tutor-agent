from __future__ import annotations

from stem_tutor.providers.base import LLMProvider
from stem_tutor.providers.mock_provider import MockProvider
from stem_tutor.providers.openai_compatible_provider import OpenAICompatibleProvider
from stem_tutor.settings import ProviderSettings


def create_provider(
    provider_name: str,
    settings: ProviderSettings,
    model_group: str = "reasoning",
    baseline_name: str | None = None,
) -> LLMProvider:
    choice = provider_name.lower().strip()
    model_name = settings.resolve_model_name(model_group=model_group, baseline_name=baseline_name)

    if choice == "mock":
        return MockProvider(model_group=model_group, model_name=f"mock-{model_name}")

    if choice in ("real", "openai-compatible"):
        try:
            return OpenAICompatibleProvider(settings, model_name=model_name, model_group=model_group)
        except Exception:
            if settings.allow_mock_fallback:
                return MockProvider(model_group=model_group, model_name=f"mock-{model_name}")
            raise

    raise ValueError(f"Unsupported provider: {provider_name}")
