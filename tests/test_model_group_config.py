from stem_tutor.providers.factory import create_provider
from stem_tutor.settings import ProviderSettings


def test_settings_resolve_model_groups():
    settings = ProviderSettings(
        provider_type="mock",
        reasoning_model_name="DeepSeek-R1-0528",
        ocr_model_name="Kimi-K2.5",
        baseline_glm5_model_name="GLM-5-Turbo",
        baseline_kimi_model_name="Kimi-K2.5",
    )

    assert settings.resolve_model_name("reasoning") == "DeepSeek-R1-0528"
    assert settings.resolve_model_name("ocr") == "Kimi-K2.5"
    assert settings.resolve_model_name("baseline", baseline_name="glm5") == "GLM-5-Turbo"
    assert settings.resolve_model_name("baseline", baseline_name="kimi") == "Kimi-K2.5"


def test_factory_supports_model_group_for_mock():
    settings = ProviderSettings(provider_type="mock")

    reasoning_provider = create_provider("mock", settings, model_group="reasoning")
    ocr_provider = create_provider("mock", settings, model_group="ocr")
    baseline_provider = create_provider("mock", settings, model_group="baseline", baseline_name="glm5")

    assert "Kimi" in reasoning_provider.provider_info()["model_name"]
    assert "Kimi" in ocr_provider.provider_info()["model_name"]
    assert "GLM-5" in baseline_provider.provider_info()["model_name"]
