import json

from stem_tutor.evaluation.convert_testdata_to_batch import (
    load_errors,
    sample_correct_questions,
    build_batch_payload,
    build_gold_labels,
    convert,
)

_ERRORS_JSONL = (
    '{"id": "test_err_1", "course": "\u5fae\u79ef\u5206A(1)", "semester": "2023-2024-1", '
    '"homework_title": "\u7b2c12\u6b21\u4f5c\u4e1a", "homework_grade": "19.5", "full_score": 20.0, '
    '"question_idx": "3(2)", "question": "test question 1", "my_answer": "test answer 1", '
    '"is_correct": false, "error_type": "\u8ba1\u7b97\u9519\u8bef-\u6f0f\u4e58\u7cfb\u6570", "error_explanation": "explanation 1", '
    '"reference": "ref 1", "reference_source": "attached", "ta_annotation": "", '
    '"ta_comment": "err", "label_source": "ta_comment", "alignment": "aligned", '
    '"source_files": ["submit"]}\n'
)

_CORRECT_LINES = []
for i in range(1, 7):
    hw_titles = ["\u7b2c1\u6b21\u4f5c\u4e1a", "\u7b2c7\u6b21\u4f5c\u4e1a", "\u7b2c8\u6b21\u4f5c\u4e1a", "\u7b2c12\u6b21\u4f5c\u4e1a", "\u7b2c1\u6b21\u4f5c\u4e1a", "\u7b2c7\u6b21\u4f5c\u4e1a"]
    _CORRECT_LINES.append(
        json.dumps({
            "id": f"test_ok_{i}", "course": "\u5fae\u79ef\u5206A(1)", "semester": "2023-2024-1",
            "homework_title": hw_titles[i - 1], "question_idx": str(i),
            "question": f"correct q{i}", "my_answer": f"correct a{i}",
            "is_correct": True, "error_type": "", "reference": f"ref_ok_{i}",
            "reference_source": "attached", "label_source": "ta_comment",
            "alignment": "aligned", "source_files": ["submit"],
        }, ensure_ascii=False)
    )
_CORRECT_JSONL = "\n".join(_CORRECT_LINES) + "\n"


def test_load_errors(tmp_path):
    f = tmp_path / "errors.jsonl"
    f.write_text(_ERRORS_JSONL, encoding="utf-8")
    errors = load_errors(f)
    assert len(errors) == 1
    assert errors[0]["id"] == "test_err_1"
    assert errors[0]["is_correct"] is False


def test_sample_correct_questions(tmp_path):
    f = tmp_path / "hw.jsonl"
    f.write_text(_CORRECT_JSONL, encoding="utf-8")
    sampled = sample_correct_questions(f, n=5, seed=42)
    assert len(sampled) == 5
    assert all(q["is_correct"] for q in sampled)
    assert all(q["reference_source"] == "attached" for q in sampled)


def test_build_batch_payload():
    errors = [json.loads(_ERRORS_JSONL.strip())]
    correct = [json.loads(line) for line in _CORRECT_JSONL.strip().split("\n")][:2]
    payload = build_batch_payload(errors + correct)
    assert "settings" in payload
    assert payload["settings"]["subject_id"] == "calculus"
    assert payload["settings"]["mode"] == "workflow_r1"
    assert len(payload["items"]) == 3
    assert payload["items"][0]["problem_text"] == "test question 1"
    assert payload["items"][0]["student_solution"] == "test answer 1"
    assert payload["items"][0]["source_type"] == "text"


def test_build_gold_labels():
    errors = [json.loads(_ERRORS_JSONL.strip())]
    correct = [json.loads(line) for line in _CORRECT_JSONL.strip().split("\n")][:2]
    labels = build_gold_labels(errors, correct)
    assert len(labels) == 3
    assert labels[0]["gold_is_correct"] is False
    assert labels[0]["gold_error_code"] == "COEFFICIENT_OMISSION"
    assert labels[0]["seq"] == 0
    assert labels[1]["gold_is_correct"] is True
    assert labels[1]["gold_error_code"] == ""
    assert labels[1]["seq"] == 1


def test_convert_end_to_end(tmp_path):
    errors_f = tmp_path / "errors.jsonl"
    hw_f = tmp_path / "hw.jsonl"
    out_dir = tmp_path / "output"
    errors_f.write_text(_ERRORS_JSONL, encoding="utf-8")
    hw_f.write_text(_CORRECT_JSONL, encoding="utf-8")

    payload_path, labels_path = convert(errors_f, hw_f, out_dir, n_correct=3, seed=42)
    assert payload_path.exists()
    assert labels_path.exists()

    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    assert len(payload["items"]) == 4
    assert len(labels) == 4
    assert labels[0]["gold_is_correct"] is False
    assert labels[0]["gold_error_code"] == "COEFFICIENT_OMISSION"
