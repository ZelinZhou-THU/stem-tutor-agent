from stem_tutor.domain.models import VerificationLabel, VerificationResult
from stem_tutor.graph.workflow import _route_after_parse, _route_after_verify


def test_route_after_parse_stop():
    assert _route_after_parse({"fail_reason": "bad parse"}) == "stop"


def test_route_after_parse_continue():
    assert _route_after_parse({}) == "continue"


def test_route_after_verify_low_confidence_stop():
    assert _route_after_verify({"uncertainty_flags": ["too_many_low_confidence_steps"]}) == "low_conf_stop"


def test_route_after_verify_need_diagnosis():
    results = [
        VerificationResult(
            step_id="S1",
            label=VerificationLabel.INCORRECT_MATH,
            evidence="x",
            confidence=0.8,
        )
    ]
    assert _route_after_verify({"verification_results": results}) == "need_diagnosis"


def test_route_after_verify_skip_diagnosis():
    results = [
        VerificationResult(
            step_id="S1",
            label=VerificationLabel.CORRECT,
            evidence="x",
            confidence=0.8,
        )
    ]
    assert _route_after_verify({"verification_results": results}) == "skip_diagnosis"
