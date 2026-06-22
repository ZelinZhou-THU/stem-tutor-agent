import json
import sqlite3
from pathlib import Path

import pytest

from stem_tutor.evaluation.eval_batch_results import (
    load_batch_results,
    evaluate_single_case,
    compute_aggregate_metrics,
    evaluate,
)


def _make_test_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE batches (id TEXT PRIMARY KEY, user_id INTEGER, status TEXT, total_count INTEGER, completed_count INTEGER, failed_count INTEGER);
        CREATE TABLE batch_items (id TEXT, batch_id TEXT, seq INTEGER, status TEXT, run_id TEXT);
        CREATE TABLE runs (id TEXT PRIMARY KEY, user_id INTEGER, data TEXT, status TEXT);
        INSERT INTO batches VALUES ('batch-1', 1, 'completed', 2, 2, 0);
        INSERT INTO batch_items VALUES ('item-0', 'batch-1', 0, 'completed', 'run-0');
        INSERT INTO batch_items VALUES ('item-1', 'batch-1', 1, 'completed', 'run-1');
    """)
    run_0_data = {
        "steps": [
            {"step_id": "S1", "raw_text": "step 1", "label": "correct"},
            {"step_id": "S2", "raw_text": "step 2", "label": "incorrect_math", "evidence": "wrong"},
        ],
        "diagnoses": [
            {"step_id": "S2", "error_code": "COEFFICIENT_OMISSION", "category": "Algebraic Manipulation Errors", "root_cause_hypothesis": "dropped coeff"},
        ],
        "reference_solution": {"steps": [{"text": "ref step"}]},
        "review_problems": [{"problem": "review q"}],
    }
    run_1_data = {
        "steps": [
            {"step_id": "S1", "raw_text": "all good", "label": "correct"},
        ],
        "diagnoses": [],
        "reference_solution": None,
        "review_problems": [],
    }
    conn.execute("INSERT INTO runs VALUES ('run-0', 1, ?, 'success')", (json.dumps(run_0_data),))
    conn.execute("INSERT INTO runs VALUES ('run-1', 1, ?, 'success')", (json.dumps(run_1_data),))
    conn.commit()
    conn.close()
    return db_path


def _make_gold_labels() -> list[dict]:
    return [
        {
            "id": "err_1", "seq": 0, "gold_is_correct": False,
            "gold_error_code": "COEFFICIENT_OMISSION",
            "gold_error_category": "Algebraic Manipulation Errors",
            "gold_error_explanation": "student dropped coeff",
            "gold_reference": "correct answer",
        },
        {
            "id": "ok_1", "seq": 1, "gold_is_correct": True,
            "gold_error_code": "",
            "gold_error_category": "",
            "gold_error_explanation": "",
            "gold_reference": "correct",
        },
    ]


def test_load_batch_results(tmp_path):
    db_path = _make_test_db(tmp_path)
    results = load_batch_results(db_path, "batch-1")
    assert len(results) == 2
    assert results[0]["seq"] == 0
    assert results[0]["run_id"] == "run-0"
    assert "steps" in results[0]["data"]
    assert results[0]["data"]["steps"][1]["label"] == "incorrect_math"


def test_evaluate_single_case_error_detected():
    gold = _make_gold_labels()[0]
    run_data = {
        "steps": [
            {"step_id": "S1", "label": "correct", "raw_text": "ok"},
            {"step_id": "S2", "label": "incorrect_math", "raw_text": "bad"},
        ],
        "diagnoses": [{"error_code": "COEFFICIENT_OMISSION", "category": "Algebraic Manipulation Errors", "root_cause_hypothesis": "test"}],
        "reference_solution": {"steps": []},
        "review_problems": [],
    }
    result = evaluate_single_case(gold, run_data)
    assert result["agent_detected_error"] is True
    assert result["error_code_match"] is True
    assert result["error_category_match"] is True
    assert result["classification"] == "TP"
    assert result["agent_first_error_step_id"] == "S2"


def test_evaluate_single_case_error_not_detected():
    gold = _make_gold_labels()[0]
    run_data = {
        "steps": [{"step_id": "S1", "label": "correct", "raw_text": "ok"}],
        "diagnoses": [],
        "reference_solution": None,
        "review_problems": [],
    }
    result = evaluate_single_case(gold, run_data)
    assert result["agent_detected_error"] is False
    assert result["classification"] == "FN"


def test_evaluate_single_case_false_positive():
    gold = _make_gold_labels()[1]
    run_data = {
        "steps": [{"step_id": "S1", "label": "incorrect_math", "raw_text": "flagged"}],
        "diagnoses": [{"error_code": "SIGN_ARITHMETIC_ERROR"}],
        "reference_solution": None,
        "review_problems": [],
    }
    result = evaluate_single_case(gold, run_data)
    assert result["agent_detected_error"] is True
    assert result["classification"] == "FP"


def test_evaluate_single_case_true_negative():
    gold = _make_gold_labels()[1]
    run_data = {
        "steps": [{"step_id": "S1", "label": "correct", "raw_text": "ok"}],
        "diagnoses": [],
        "reference_solution": None,
        "review_problems": [],
    }
    result = evaluate_single_case(gold, run_data)
    assert result["agent_detected_error"] is False
    assert result["classification"] == "TN"


def test_evaluate_single_case_missing_data():
    gold = _make_gold_labels()[0]
    result = evaluate_single_case(gold, None)
    assert result["classification"] == "MISSING"
    assert result["agent_detected_error"] is None


def test_compute_aggregate_metrics():
    case_results = [
        {"classification": "TP", "error_code_match": True, "error_category_match": True, "gold_is_correct": False},
        {"classification": "TP", "error_code_match": False, "error_category_match": True, "gold_is_correct": False},
        {"classification": "FN", "error_code_match": False, "error_category_match": False, "gold_is_correct": False},
        {"classification": "TN", "gold_is_correct": True},
        {"classification": "FP", "gold_is_correct": True},
    ]
    metrics = compute_aggregate_metrics(case_results)
    assert metrics["detection_recall"] == pytest.approx(2 / 3, abs=0.01)
    assert metrics["detection_precision"] == pytest.approx(2 / 3, abs=0.01)
    assert metrics["false_positive_rate"] == pytest.approx(0.5, abs=0.01)
    assert metrics["code_accuracy"] == pytest.approx(1 / 3, abs=0.01)
    assert metrics["category_accuracy"] == pytest.approx(2 / 3, abs=0.01)


def test_evaluate_integration(tmp_path):
    db_path = _make_test_db(tmp_path)
    gold_path = tmp_path / "gold_labels.json"
    gold_path.write_text(json.dumps(_make_gold_labels(), ensure_ascii=False), encoding="utf-8")
    output_path = tmp_path / "eval_results.json"

    result = evaluate(db_path, "batch-1", gold_path, output_path)
    assert result["total_cases"] == 2
    assert "detection_recall" in result
    assert "detection_precision" in result
    assert "false_positive_rate" in result
    assert len(result["case_details"]) == 2
    assert output_path.exists()
