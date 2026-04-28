from stem_tutor.nodes.verify_steps import _verify_payload_with_retry


class _RetryProvider:
    def __init__(self):
        self.calls = 0

    def verify_step(self, prompt: str):
        self.calls += 1
        if self.calls == 1:
            return {"oops": "bad-format"}
        return {
            "label": "correct",
            "evidence": "ok",
            "confidence": 0.8,
            "violated_principles": [],
        }


class _AlwaysBadProvider:
    def verify_step(self, prompt: str):
        return {"oops": "bad-format"}


def test_verify_payload_with_retry_recovers_on_second_try():
    provider = _RetryProvider()
    raw, used_fallback = _verify_payload_with_retry(provider, "dummy", retries=1)

    assert provider.calls == 2
    assert used_fallback is False
    assert raw["label"] == "correct"


def test_verify_payload_with_retry_fallback_when_all_invalid():
    provider = _AlwaysBadProvider()
    raw, used_fallback = _verify_payload_with_retry(provider, "dummy", retries=1)

    assert used_fallback is True
    assert raw["label"] == "unclear"
    assert "schema_validation" in raw["violated_principles"]
