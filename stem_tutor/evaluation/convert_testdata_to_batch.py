"""Convert TestData4StemTutor dataset to batch API format + gold labels."""

import argparse
import json
import random
from pathlib import Path

from stem_tutor.evaluation.error_type_mapping import map_error_type


def load_errors(path: Path) -> list[dict]:
    result = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                result.append(json.loads(line))
    return result


def sample_correct_questions(path: Path, n: int = 5, seed: int = 42) -> list[dict]:
    all_correct = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            q = json.loads(line)
            if q.get("is_correct") is True and q.get("reference_source") == "attached":
                all_correct.append(q)

    if len(all_correct) <= n:
        return all_correct

    rng = random.Random(seed)

    by_homework: dict[str, list[dict]] = {}
    for q in all_correct:
        hw = q.get("homework_title", "unknown")
        by_homework.setdefault(hw, []).append(q)

    homeworks = sorted(by_homework.keys())
    for hw in homeworks:
        rng.shuffle(by_homework[hw])

    sampled: list[dict] = []
    round_idx = 0
    while len(sampled) < n:
        made_progress = False
        for hw in homeworks:
            if len(sampled) >= n:
                break
            pool = by_homework[hw]
            if round_idx < len(pool):
                sampled.append(pool[round_idx])
                made_progress = True
        if not made_progress:
            break
        round_idx += 1

    return sampled[:n]


def build_batch_payload(cases: list[dict]) -> dict:
    items = []
    for q in cases:
        items.append({
            "problem_text": q.get("question", ""),
            "student_solution": q.get("my_answer", ""),
            "source_type": "text",
        })
    return {
        "settings": {
            "model": "qwen/qwen3.6-plus",
            "subject_id": "calculus",
            "mode": "workflow_r1",
            "depth": "with_ref",
        },
        "items": items,
    }


def build_gold_labels(errors: list[dict], correct: list[dict]) -> list[dict]:
    labels = []
    seq = 0
    for q in errors:
        mapped = map_error_type(q.get("error_type", ""))
        labels.append({
            "id": q["id"],
            "seq": seq,
            "gold_is_correct": False,
            "gold_error_type_raw": q.get("error_type", ""),
            "gold_error_code": mapped.error_code,
            "gold_error_category": mapped.category,
            "gold_mapping_confidence": mapped.confidence,
            "gold_error_explanation": q.get("error_explanation", ""),
            "gold_reference": q.get("reference", ""),
            "gold_label_source": q.get("label_source", ""),
        })
        seq += 1
    for q in correct:
        labels.append({
            "id": q["id"],
            "seq": seq,
            "gold_is_correct": True,
            "gold_error_type_raw": "",
            "gold_error_code": "",
            "gold_error_category": "",
            "gold_mapping_confidence": "none",
            "gold_error_explanation": "",
            "gold_reference": q.get("reference", ""),
            "gold_label_source": q.get("label_source", ""),
        })
        seq += 1
    return labels


def convert(
    errors_path: Path,
    homework_path: Path,
    output_dir: Path,
    n_correct: int = 5,
    seed: int = 42,
) -> tuple[Path, Path]:
    errors = load_errors(errors_path)
    correct = sample_correct_questions(homework_path, n=n_correct, seed=seed)

    all_cases = errors + correct
    payload = build_batch_payload(all_cases)
    labels = build_gold_labels(errors, correct)

    output_dir.mkdir(parents=True, exist_ok=True)
    payload_path = output_dir / "batch_payload.json"
    labels_path = output_dir / "gold_labels.json"

    with open(payload_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    with open(labels_path, "w", encoding="utf-8") as f:
        json.dump(labels, f, ensure_ascii=False, indent=2)

    return payload_path, labels_path


def main():
    parser = argparse.ArgumentParser(description="Convert TestData4StemTutor to batch format")
    parser.add_argument("--errors", required=True, type=Path)
    parser.add_argument("--homework", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--n-correct", type=int, default=5)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    payload_path, labels_path = convert(
        args.errors, args.homework, args.output, args.n_correct, args.seed
    )
    print(f"batch_payload -> {payload_path}")
    print(f"gold_labels   -> {labels_path}")


if __name__ == "__main__":
    main()
