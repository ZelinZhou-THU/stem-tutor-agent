"""Combined evaluation from DB: Phase 1 (problem-level) + Phase 2 (step-level).

Reads batch run results from SQLite DB and evaluates against gold labels.
Handles both workflow_r1 and baseline_qwen modes.

Usage:
    python eval_combined_from_db.py --db data/stem_tutor.db --wf <wf_batch_id> --bl <bl_batch_id> --gold ../TestData4StemTutor/eval_output/combined_gold_labels.json --output ../TestData4StemTutor/eval_output/
"""

import argparse
import json
import sqlite3
from pathlib import Path

from stem_tutor.evaluation.runner import (
    _verification_accuracy,
    _diagnosis_hit,
    _error_step_recall,
    _taxonomy_category_hit,
    _first_error_step,
    _error_detection_rate,
    _correct_confirmation_rate,
    _feedback_proxy,
    _review_relevance_proxy,
    _normalize_items,
)

_NON_CORRECT = {"incorrect_math", "inconsistent_or_unsupported", "unclear"}


def load_batch_runs(db_path: Path, batch_id: str) -> dict[int, dict]:
    """Load all runs for a batch, keyed by seq. Returns {seq: run_data}."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        items = conn.execute(
            "SELECT seq, run_id, status FROM batch_items WHERE batch_id=? ORDER BY seq",
            (batch_id,),
        ).fetchall()
        results = {}
        for item in items:
            run_id = item["run_id"]
            if not run_id:
                results[item["seq"]] = None
                continue
            row = conn.execute(
                "SELECT data FROM runs WHERE id=?", (run_id,),
            ).fetchone()
            data = json.loads(row["data"]) if row and row["data"] else None
            results[item["seq"]] = data
        return results
    finally:
        conn.close()


def extract_verify_diagnose(run_data: dict | None) -> tuple[list, list, dict, list]:
    """Extract verification_results, diagnosis_results, feedback, reviews from run data.

    Handles both workflow (has 'steps') and baseline (has 'raw_output.verification_results').
    """
    if run_data is None:
        return [], [], {}, []

    # Try raw_output first (most complete)
    raw = run_data.get("raw_output", {})
    if isinstance(raw, dict):
        vr = _normalize_items(raw.get("verification_results", []))
        dr = _normalize_items(raw.get("diagnosis_results", []))
        fb = raw.get("final_feedback", {})
        if hasattr(fb, "model_dump"):
            fb = fb.model_dump()
        rp = _normalize_items(raw.get("review_problems", []))
        if vr:
            return vr, dr, fb, rp

    # Fall back to top-level shaped fields
    steps = run_data.get("steps", [])
    vr = [
        {"step_id": s.get("step_id", ""), "label": s.get("label", ""),
         "evidence": s.get("evidence", ""), "confidence": s.get("confidence", 0.0)}
        for s in steps
    ]
    dr = run_data.get("diagnoses", [])
    fb = {}
    if run_data.get("concise_summary"):
        fb = {
            "first_critical_step_id": run_data.get("first_critical_step_id"),
            "next_action": run_data.get("next_action", ""),
        }
    rp = run_data.get("review_problems", [])
    return vr, dr, fb, rp


def eval_phase1_case(gold: dict, run_data: dict | None) -> dict:
    """Evaluate a Phase 1 case (problem-level metrics)."""
    vr, dr, fb, rp = extract_verify_diagnose(run_data)

    agent_detected = any(v.get("label") in _NON_CORRECT for v in vr)
    agent_codes = {d.get("error_code", "") for d in dr if d.get("error_code")}
    gold_code = gold.get("gold_error_code", "")

    gold_is_error = not gold.get("gold_is_correct", True)
    if gold_is_error and agent_detected:
        classification = "TP"
    elif gold_is_error and not agent_detected:
        classification = "FN"
    elif not gold_is_error and agent_detected:
        classification = "FP"
    else:
        classification = "TN"

    code_match = bool(gold_code) and gold_code in agent_codes if gold_code else False

    return {
        "case_id": gold.get("id", "?"),
        "classification": classification,
        "agent_detected_error": agent_detected,
        "error_code_match": code_match,
        "agent_diagnosis_codes": sorted(agent_codes),
        "gold_error_code": gold_code,
    }


def eval_phase2_case(gold: dict, run_data: dict | None) -> dict:
    """Evaluate a Phase 2 case (step-level metrics)."""
    vr, dr, fb, rp = extract_verify_diagnose(run_data)

    gold_verify = gold.get("gold_verification", [])
    gold_codes = gold.get("gold_diagnosis_codes", [])
    expected_first = gold.get("gold_first_error_step")

    verify_acc = _verification_accuracy(vr, gold_verify)
    diag_hit = _diagnosis_hit(dr, gold_codes)
    error_recall = _error_step_recall(vr, gold_verify)
    cat_hit = _taxonomy_category_hit(dr, gold_codes)
    pred_first = _first_error_step(vr)
    first_hit = 1.0 if expected_first == pred_first else 0.0
    fb_proxy = _feedback_proxy(fb, expected_first)
    rv_proxy = _review_relevance_proxy(rp, gold_codes)
    det_rate = _error_detection_rate(vr, gold_verify)
    conf_rate = _correct_confirmation_rate(vr, gold_verify)

    return {
        "case_id": gold.get("case_id", "?"),
        "verification_accuracy": verify_acc,
        "diagnosis_hit": diag_hit,
        "error_step_recall": error_recall,
        "taxonomy_category_hit": cat_hit,
        "first_error_hit": first_hit,
        "feedback_proxy": fb_proxy,
        "review_relevance_proxy": rv_proxy,
        "error_detection_rate": det_rate,
        "correct_confirmation_rate": conf_rate,
    }


def aggregate(rows: list[dict], keys: list[str]) -> dict:
    n = len(rows)
    return {k: round(sum(r.get(k, 0) for r in rows) / n, 4) for k in keys} if n else {}


def evaluate_mode(runs: dict, gold: dict) -> dict:
    """Evaluate one mode (workflow or baseline) against combined gold labels."""
    p1_gold = gold.get("phase1", [])
    p2_gold = gold.get("phase2", [])

    # Phase 1: problem-level
    p1_results = []
    for g in p1_gold:
        seq = g["batch_seq"]
        run_data = runs.get(seq)
        p1_results.append(eval_phase1_case(g, run_data))

    tp = sum(1 for r in p1_results if r["classification"] == "TP")
    fn = sum(1 for r in p1_results if r["classification"] == "FN")
    fp = sum(1 for r in p1_results if r["classification"] == "FP")
    tn = sum(1 for r in p1_results if r["classification"] == "TN")
    error_cases = [r for r in p1_results if not r.get("gold_error_code") == ""]
    code_matches = sum(1 for r in error_cases if r["error_code_match"])

    p1_summary = {
        "tp": tp, "fn": fn, "fp": fp, "tn": tn,
        "detection_recall": round(tp / (tp + fn), 4) if (tp + fn) else 0,
        "false_positive_rate": round(fp / (fp + tn), 4) if (fp + tn) else 0,
        "code_accuracy": round(code_matches / len(error_cases), 4) if error_cases else 0,
    }

    # Phase 2: step-level
    p2_results = []
    for g in p2_gold:
        seq = g["batch_seq"]
        run_data = runs.get(seq)
        p2_results.append(eval_phase2_case(g, run_data))

    p2_keys = [
        "verification_accuracy", "diagnosis_hit", "error_step_recall",
        "taxonomy_category_hit", "first_error_hit", "feedback_proxy",
        "review_relevance_proxy", "error_detection_rate", "correct_confirmation_rate",
    ]
    p2_summary = aggregate(p2_results, p2_keys)

    return {
        "phase1": p1_summary,
        "phase1_details": p1_results,
        "phase2": p2_summary,
        "phase2_details": p2_results,
    }


def print_comparison(wf: dict, bl: dict) -> None:
    print("\n" + "=" * 70)
    print("  PHASE 1: Real Homework (Problem-Level)")
    print("=" * 70)
    p1w, p1b = wf["phase1"], bl["phase1"]
    print(f"  {'Metric':<30} {'Agent':>10} {'Baseline':>10}")
    print("-" * 55)
    print(f"  {'TP':.<30} {p1w['tp']:>10} {p1b['tp']:>10}")
    print(f"  {'FN':.<30} {p1w['fn']:>10} {p1b['fn']:>10}")
    print(f"  {'FP':.<30} {p1w['fp']:>10} {p1b['fp']:>10}")
    print(f"  {'TN':.<30} {p1w['tn']:>10} {p1b['tn']:>10}")
    print(f"  {'Detection Recall':.<30} {p1w['detection_recall']:>10.1%} {p1b['detection_recall']:>10.1%}")
    print(f"  {'False Positive Rate':.<30} {p1w['false_positive_rate']:>10.1%} {p1b['false_positive_rate']:>10.1%}")
    print(f"  {'Code Accuracy':.<30} {p1w['code_accuracy']:>10.1%} {p1b['code_accuracy']:>10.1%}")

    print("\n" + "=" * 70)
    print("  PHASE 2: Planted Errors (Step-Level)")
    print("=" * 70)
    p2w, p2b = wf["phase2"], bl["phase2"]
    labels = {
        "verification_accuracy": "Verification Accuracy",
        "diagnosis_hit": "Diagnosis Hit",
        "error_step_recall": "Error Step Recall",
        "taxonomy_category_hit": "Category Hit",
        "first_error_hit": "First Error Step Hit",
        "feedback_proxy": "Feedback Proxy",
        "review_relevance_proxy": "Review Relevance",
        "error_detection_rate": "Problem-Level Detection",
        "correct_confirmation_rate": "Problem-Level Confirm",
    }
    print(f"  {'Metric':<30} {'Agent':>10} {'Baseline':>10} {'Delta':>8}")
    print("-" * 65)
    for k, label in labels.items():
        wv = p2w.get(k, 0)
        bv = p2b.get(k, 0)
        delta = wv - bv
        sign = "+" if delta >= 0 else ""
        print(f"  {label:.<30} {wv:>10.4f} {bv:>10.4f} {sign}{delta:>7.4f}")
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Combined evaluation from DB")
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--wf", required=True, help="workflow batch ID")
    parser.add_argument("--bl", required=True, help="baseline batch ID")
    parser.add_argument("--gold", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    gold = json.loads(args.gold.read_text(encoding="utf-8"))

    print("Loading workflow runs...")
    wf_runs = load_batch_runs(args.db, args.wf)
    print(f"  {len(wf_runs)} runs loaded")

    print("Loading baseline runs...")
    bl_runs = load_batch_runs(args.db, args.bl)
    print(f"  {len(bl_runs)} runs loaded")

    wf_result = evaluate_mode(wf_runs, gold)
    bl_result = evaluate_mode(bl_runs, gold)

    print_comparison(wf_result, bl_result)

    output = {"workflow": wf_result, "baseline": bl_result}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    out_path = args.output / "combined_eval_results.json"
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
