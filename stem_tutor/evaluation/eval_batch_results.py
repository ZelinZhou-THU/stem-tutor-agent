"""Evaluate batch results from database against gold labels."""

import argparse
import json
import sqlite3
from pathlib import Path

_NON_CORRECT_LABELS = {"incorrect_math", "inconsistent_or_unsupported"}


def load_batch_results(db_path: Path, batch_id: str) -> list[dict]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        items = conn.execute(
            "SELECT seq, run_id, status FROM batch_items WHERE batch_id=? ORDER BY seq",
            (batch_id,),
        ).fetchall()

        results = []
        for item in items:
            run_id = item["run_id"]
            if not run_id:
                results.append({
                    "seq": item["seq"],
                    "run_id": None,
                    "data": None,
                    "item_status": item["status"],
                })
                continue
            row = conn.execute(
                "SELECT data, status FROM runs WHERE id=?",
                (run_id,),
            ).fetchone()
            data = json.loads(row["data"]) if row and row["data"] else None
            results.append({
                "seq": item["seq"],
                "run_id": run_id,
                "data": data,
                "item_status": item["status"],
                "run_status": row["status"] if row else None,
            })
        return results
    finally:
        conn.close()


def evaluate_single_case(gold: dict, run_data: dict | None) -> dict:
    if run_data is None:
        return {
            "id": gold["id"],
            "seq": gold["seq"],
            "gold_is_correct": gold["gold_is_correct"],
            "classification": "MISSING",
            "agent_detected_error": None,
            "error_code_match": False,
            "error_category_match": False,
            "agent_diagnosis_codes": [],
            "agent_first_error_step_id": None,
            "agent_first_error_step_text": "",
            "agent_root_cause": "",
            "gold_error_explanation": gold.get("gold_error_explanation", ""),
            "reference_generated": False,
            "review_problems_count": 0,
            "note": "run result missing",
        }

    steps = run_data.get("steps", [])
    diagnoses = run_data.get("diagnoses", [])
    ref_sol = run_data.get("reference_solution")
    reviews = run_data.get("review_problems", [])

    agent_detected = any(
        s.get("label") in _NON_CORRECT_LABELS for s in steps
    )

    first_error_step = next(
        (s for s in steps if s.get("label") in _NON_CORRECT_LABELS),
        None,
    )

    agent_codes = {d.get("error_code", "") for d in diagnoses if d.get("error_code")}
    agent_categories = {d.get("category", "") for d in diagnoses if d.get("category")}

    gold_code = gold.get("gold_error_code", "")
    gold_category = gold.get("gold_error_category", "")

    code_match = bool(gold_code) and gold_code in agent_codes if gold_code else False
    category_match = bool(gold_category) and gold_category in agent_categories if gold_category else False

    gold_is_error = not gold["gold_is_correct"]
    if gold_is_error and agent_detected:
        classification = "TP"
    elif gold_is_error and not agent_detected:
        classification = "FN"
    elif not gold_is_error and agent_detected:
        classification = "FP"
    else:
        classification = "TN"

    root_cause = ""
    if diagnoses:
        root_cause = diagnoses[0].get("root_cause_hypothesis", "")

    return {
        "id": gold["id"],
        "seq": gold["seq"],
        "gold_is_correct": gold["gold_is_correct"],
        "classification": classification,
        "agent_detected_error": agent_detected,
        "error_code_match": code_match,
        "error_category_match": category_match,
        "agent_diagnosis_codes": sorted(agent_codes),
        "agent_first_error_step_id": first_error_step.get("step_id") if first_error_step else None,
        "agent_first_error_step_text": first_error_step.get("raw_text", "") if first_error_step else "",
        "agent_root_cause": root_cause,
        "gold_error_explanation": gold.get("gold_error_explanation", ""),
        "reference_generated": ref_sol is not None,
        "review_problems_count": len(reviews),
    }


def compute_aggregate_metrics(case_results: list[dict]) -> dict:
    tp = sum(1 for c in case_results if c["classification"] == "TP")
    fn = sum(1 for c in case_results if c["classification"] == "FN")
    fp = sum(1 for c in case_results if c["classification"] == "FP")
    tn = sum(1 for c in case_results if c["classification"] == "TN")
    missing = sum(1 for c in case_results if c["classification"] == "MISSING")

    error_cases = [c for c in case_results if not c["gold_is_correct"]]
    code_matches = sum(1 for c in error_cases if c.get("error_code_match"))
    category_matches = sum(1 for c in error_cases if c.get("error_category_match"))

    detection_recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    detection_precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    false_positive_rate = fp / (fp + tn) if (fp + tn) > 0 else 0.0
    code_accuracy = code_matches / len(error_cases) if error_cases else 0.0
    category_accuracy = category_matches / len(error_cases) if error_cases else 0.0

    return {
        "total_cases": len(case_results),
        "tp": tp,
        "fn": fn,
        "fp": fp,
        "tn": tn,
        "missing": missing,
        "detection_recall": round(detection_recall, 4),
        "detection_precision": round(detection_precision, 4),
        "false_positive_rate": round(false_positive_rate, 4),
        "code_accuracy": round(code_accuracy, 4),
        "category_accuracy": round(category_accuracy, 4),
    }


def evaluate(
    db_path: Path,
    batch_id: str,
    gold_path: Path,
    output_path: Path,
) -> dict:
    gold_labels = json.loads(gold_path.read_text(encoding="utf-8"))
    batch_results = load_batch_results(db_path, batch_id)

    results_by_seq = {r["seq"]: r for r in batch_results}

    case_details = []
    for gold in gold_labels:
        seq = gold["seq"]
        run_result = results_by_seq.get(seq)
        run_data = run_result["data"] if run_result else None
        case_details.append(evaluate_single_case(gold, run_data))

    metrics = compute_aggregate_metrics(case_details)

    result = {
        "batch_id": batch_id,
        **metrics,
        "case_details": case_details,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return result


def _print_summary(result: dict) -> None:
    m = result
    print("=" * 60)
    print(f"Eval: {m['total_cases']} cases (TP={m['tp']} FN={m['fn']} FP={m['fp']} TN={m['tn']})")
    print(f"  detection_recall:     {m['detection_recall']:.1%}")
    print(f"  detection_precision:  {m['detection_precision']:.1%}")
    print(f"  false_positive_rate:  {m['false_positive_rate']:.1%}")
    print(f"  code_accuracy:        {m['code_accuracy']:.1%}")
    print(f"  category_accuracy:    {m['category_accuracy']:.1%}")
    print("=" * 60)
    print("\nPer-case details:")
    for c in m["case_details"]:
        print(f"  [{c['classification']}] {c['id']}")
        if c.get("agent_detected_error"):
            print(f"       codes: {c['agent_diagnosis_codes']}")
            print(f"       first_err_step: {c['agent_first_error_step_id']} - {c['agent_first_error_step_text'][:60]}")
            print(f"       root_cause: {c['agent_root_cause'][:80]}")
        if not c["gold_is_correct"]:
            print(f"       gold_explanation: {c['gold_error_explanation'][:80]}")


def main():
    parser = argparse.ArgumentParser(description="Evaluate batch results")
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--gold", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    result = evaluate(args.db, args.batch_id, args.gold, args.output)
    _print_summary(result)
    print(f"\nDetailed results saved to {args.output}")


if __name__ == "__main__":
    main()
