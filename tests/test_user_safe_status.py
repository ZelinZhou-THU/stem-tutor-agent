from web.service import _shape_response


def test_shape_response_maps_manual_review_to_needs_review_user_status():
    state = {
        "normalized_steps": [],
        "verification_results": [],
        "diagnosis_results": [],
        "review_problems": [],
        "uncertainty_flags": ["manual_review_required"],
        "run_meta": {"run_id": "r1"},
    }

    out = _shape_response(state)

    assert out["status"] == "manual_review_required"
    assert out["user_status"] == "needs_review"
    assert out["run_meta"]["failed"] is False


def test_shape_response_maps_fail_to_unavailable_user_status():
    state = {
        "normalized_steps": [],
        "verification_results": [],
        "diagnosis_results": [],
        "review_problems": [],
        "fail_reason": "provider timeout",
        "uncertainty_flags": [],
        "run_meta": {"run_id": "r2"},
    }

    out = _shape_response(state)

    assert out["status"] == "failed"
    assert out["user_status"] == "unavailable"
    assert out["run_meta"]["failed"] is True
