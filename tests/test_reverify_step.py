import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from stem_tutor.graph.strategy import StrategyOutcome
from stem_tutor.domain.models import VerificationLabel
from web.service import reverify_step, _update_step_in_run


def _make_run_data(run_id="test-run-001"):
    return {
        "steps": [
            {"step_id": "S1", "label": "unclear", "confidence": 0.25, "evidence": "budget limited",
             "violated_principles": ["verification_default_label"], "sympy_verified": False, "sympy_equivalent": None,
             "raw_text": "x = 3 + 5"},
        ],
        "raw_output": {
            "normalized_steps": [
                {"step_id": "S1", "normalized_text": "x = 3 + 5", "raw_text": "x = 3 + 5"},
            ],
            "problem_input": {"problem_text": "Compute x."},
            "reference_solution": {"reference_text": "x = 8", "key_assertions": ["x = 8"]},
            "verification_results": [
                {"step_id": "S1", "label": "unclear", "confidence": 0.25, "evidence": "budget limited",
                 "violated_principles": ["verification_default_label"]},
            ],
        },
        "run_meta": {"run_id": run_id},
    }


def _mock_outcome():
    return StrategyOutcome(
        data={"label": "correct", "evidence": "Mock verified", "confidence": 0.9, "violated_principles": []},
        quality="full",
        confidence=0.9,
        strategy_name="pure_llm",
        elapsed_seconds=0.1,
    )


def test_reverify_nonexistent_run(tmp_path):
    with patch("web.service.RUNS_DIR", tmp_path):
        result = reverify_step("nonexistent", "S1")
        assert result["success"] is False
        assert "不存在" in result["error"]


def test_reverify_nonexistent_step(tmp_path):
    run_data = _make_run_data()
    run_file = tmp_path / "test-run-001.json"
    run_file.write_text(json.dumps(run_data, ensure_ascii=False), encoding="utf-8")

    with patch("web.service.RUNS_DIR", tmp_path):
        result = reverify_step("test-run-001", "S99")
        assert result["success"] is False
        assert "不存在" in result["error"]


def test_reverify_success_structure(tmp_path):
    run_data = _make_run_data()
    run_file = tmp_path / "test-run-001.json"
    run_file.write_text(json.dumps(run_data, ensure_ascii=False), encoding="utf-8")

    mock_provider = MagicMock()

    with patch("web.service.RUNS_DIR", tmp_path), \
         patch("web.service.load_provider_settings") as mock_settings, \
         patch("web.service.create_provider", return_value=mock_provider), \
         patch("stem_tutor.graph.strategy.StrategyChain.execute", return_value=_mock_outcome()):
        mock_settings.return_value = MagicMock(verify_model_group="fast", verify_model_name=None)
        result = reverify_step("test-run-001", "S1")
        assert result["success"] is True
        assert "verification_result" in result
        assert "elapsed_seconds" in result


def test_reverify_history_recorded(tmp_path):
    run_data = _make_run_data()
    run_file = tmp_path / "test-run-001.json"
    run_file.write_text(json.dumps(run_data, ensure_ascii=False), encoding="utf-8")

    mock_provider = MagicMock()

    with patch("web.service.RUNS_DIR", tmp_path), \
         patch("web.service.load_provider_settings") as mock_settings, \
         patch("web.service.create_provider", return_value=mock_provider), \
         patch("stem_tutor.graph.strategy.StrategyChain.execute", return_value=_mock_outcome()):
        mock_settings.return_value = MagicMock(verify_model_group="fast", verify_model_name=None)
        reverify_step("test-run-001", "S1")

        updated = json.loads(run_file.read_text(encoding="utf-8"))
        assert "reverify_history" in updated
        assert len(updated["reverify_history"]) == 1
        assert updated["reverify_history"][0]["step_id"] == "S1"
