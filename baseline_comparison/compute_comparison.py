"""Reproducible evaluation script for STEM Tutor Agent vs Baseline comparison.

Reads pre-exported evaluation data (agent_eval_data.json / baseline_eval_data.json)
and computes both Phase 1 (problem-level) and Phase 2 (step-level) comparison metrics.

Usage:
    cd baseline_comparison
    python compute_comparison.py

Output:
    - Console: formatted comparison tables
    - comparison_results.json: full results with per-item details
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stem_tutor.evaluation.runner import (
    _verification_accuracy,
    _diagnosis_hit,
    _error_step_recall,
    _taxonomy_category_hit,
    _first_error_step,
    _feedback_proxy,
    _review_relevance_proxy,
    _error_step_accuracy,
    _correct_step_accuracy,
    _lenient_verification_accuracy,
)

DATA_DIR = Path(__file__).resolve().parent
AGENT_FILE = DATA_DIR / "agent_eval_data.json"
BASELINE_FILE = DATA_DIR / "baseline_eval_data.json"
OUTPUT_FILE = DATA_DIR / "comparison_results.json"

NON_CORRECT = {"incorrect_math", "inconsistent_or_unsupported", "unclear"}


# ─────────────────────────────── Phase 1 ───────────────────────────────


def compute_phase1(items: list[dict]) -> dict:
    """Problem-level metrics for real homework (10 items).

    Returns dict with tp, fn, fp, tn counts and derived rates.
    """
    tp = fn = fp = tn = 0
    code_matches = 0  # exact taxonomy code match for error cases

    for item in items:
        gold = item["gold_labels"]
        summary = item["run_summary"]
        actual_error = not gold["is_correct"]

        steps = summary.get("steps", [])
        predicted_error = any(s.get("label", "") != "correct" for s in steps)

        if actual_error and predicted_error:
            tp += 1
        elif actual_error and not predicted_error:
            fn += 1
        elif not actual_error and predicted_error:
            fp += 1
        else:
            tn += 1

        if predicted_error and actual_error and gold.get("error_code"):
            pred_codes = {d.get("error_code", "") for d in summary.get("diagnoses", [])}
            if gold["error_code"] in pred_codes and gold["error_code"]:
                code_matches += 1

    total_errors = tp + fn
    total_correct = tn + fp

    return {
        "tp": tp,
        "fn": fn,
        "fp": fp,
        "tn": tn,
        "detection_recall": round(tp / total_errors, 4) if total_errors else 1.0,
        "false_positive_rate": round(fp / total_correct, 4) if total_correct else 0.0,
        "code_accuracy": round(code_matches / total_errors, 4) if total_errors else 1.0,
    }


# ─────────────────────────────── Phase 2 ───────────────────────────────


def compute_phase2(items: list[dict]) -> dict:
    """Step-level metrics for planted errors (12 items).

    Computes aggregate averages across all 12 items.
    """
    agg = {
        "verification_accuracy": [],
        "lenient_verification_accuracy": [],
        "error_step_accuracy": [],
        "correct_step_accuracy": [],
        "diagnosis_hit": [],
        "error_step_recall": [],
        "taxonomy_category_hit": [],
        "first_error_hit": [],
        "feedback_proxy": [],
        "review_relevance_proxy": [],
    }

    for item in items:
        gold = item["gold_labels"]
        summary = item["run_summary"]

        gold_verify = gold.get("gold_verification", [])
        pred_verify = summary.get("steps", [])
        gold_codes = gold.get("gold_diagnosis_codes", [])
        pred_diag = summary.get("diagnoses", [])
        feedback = summary.get("final_feedback", {})
        reviews = summary.get("review_problems", [])

        agg["verification_accuracy"].append(_verification_accuracy(pred_verify, gold_verify))
        agg["lenient_verification_accuracy"].append(
            _lenient_verification_accuracy(pred_verify, gold_verify)
        )
        agg["error_step_accuracy"].append(_error_step_accuracy(pred_verify, gold_verify))
        agg["correct_step_accuracy"].append(_correct_step_accuracy(pred_verify, gold_verify))
        agg["diagnosis_hit"].append(_diagnosis_hit(pred_diag, gold_codes))
        agg["error_step_recall"].append(_error_step_recall(pred_verify, gold_verify))
        agg["taxonomy_category_hit"].append(_taxonomy_category_hit(pred_diag, gold_codes))
        expected_first = gold.get("gold_first_error_step")
        pred_first = _first_error_step(pred_verify)
        agg["first_error_hit"].append(
            1.0 if expected_first == pred_first else 0.0
        )
        agg["feedback_proxy"].append(
            _feedback_proxy(feedback, gold.get("gold_first_error_step"))
        )
        agg["review_relevance_proxy"].append(_review_relevance_proxy(reviews, gold_codes))

    result = {}
    for key, vals in agg.items():
        result[key] = round(sum(vals) / len(vals), 4) if vals else 0.0
    return result


# ─────────────────────────────── Display ───────────────────────────────


def print_comparison(agent_p1, bl_p1, agent_p2, bl_p2):
    """Print formatted comparison tables."""
    print()
    print("=" * 72)
    print("  STEM Tutor Evaluation: Agent vs Baseline")
    print("  Model: Qwen 3.6 Plus (qwen/qwen3.6-plus)")
    print("=" * 72)

    # Phase 1 table
    print()
    print("Phase 1 — Real Homework (Problem-Level)")
    print("-" * 60)
    print(f"        {'TP':>3}  {'FN':>3}  {'FP':>3}  {'TN':>3}  "
          f"{'Recall':>7}  {'FPR':>6}  {'Code Acc':>8}")
    print("-" * 60)
    _print_row("Agent   ", agent_p1)
    _print_row("Baseline", bl_p1)
    print("-" * 60)

    # Phase 2 table
    print()
    print("Phase 2 — Planted Errors (Step-Level)")
    print("-" * 62)
    print(f"  {'Metric':<30} {'Agent':>8}  {'Baseline':>8}")
    print("-" * 62)
    p2_keys = [
        ("verification_accuracy", "Strict Verification Acc"),
        ("lenient_verification_accuracy", "Lenient Verification Acc"),
        ("error_step_accuracy", "Error-Step Accuracy"),
        ("correct_step_accuracy", "Correct-Step Accuracy"),
        ("diagnosis_hit", "Diagnosis Hit"),
        ("error_step_recall", "Error Step Recall"),
        ("taxonomy_category_hit", "Taxonomy Category Hit"),
        ("first_error_hit", "First Error Step Hit"),
    ]
    for key, label in p2_keys:
        a = agent_p2.get(key, 0)
        b = bl_p2.get(key, 0)
        delta = a - b
        marker = (
            " +" if delta > 0.001 else (" -" if delta < -0.001 else " =")
        )
        print(f"  {label:<30} {a:>7.4f}  {b:>7.4f}  {delta:+7.4f}{marker}")
    print("-" * 62)
    print("  Legend: + Agent better   - Baseline better   = Tie")


def _print_row(label, p1):
    print(
        f"{label}"
        f" {p1['tp']:>3}  {p1['fn']:>3}  {p1['fp']:>3}  {p1['tn']:>3}  "
        f"{p1['detection_recall']:>6.1%}  {p1['false_positive_rate']:>5.1%}  "
        f"{p1['code_accuracy']:>8.1%}"
    )


# ─────────────────────────────── Main ───────────────────────────────


def main():
    if not AGENT_FILE.exists():
        print(f"ERROR: {AGENT_FILE} not found", file=sys.stderr)
        sys.exit(1)
    if not BASELINE_FILE.exists():
        print(f"ERROR: {BASELINE_FILE} not found", file=sys.stderr)
        sys.exit(1)

    agent_data = json.loads(AGENT_FILE.read_text(encoding="utf-8"))
    baseline_data = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))

    agent_items = agent_data["items"]
    baseline_items = baseline_data["items"]

    # Split items by phase
    agent_p1 = [i for i in agent_items if i["phase"] == "phase1_real_homework"]
    agent_p2 = [i for i in agent_items if i["phase"] == "phase2_planted_error"]
    bl_p1 = [i for i in baseline_items if i["phase"] == "phase1_real_homework"]
    bl_p2 = [i for i in baseline_items if i["phase"] == "phase2_planted_error"]

    a_p1 = compute_phase1(agent_p1)
    b_p1 = compute_phase1(bl_p1)
    a_p2 = compute_phase2(agent_p2)
    b_p2 = compute_phase2(bl_p2)

    print_comparison(a_p1, b_p1, a_p2, b_p2)

    output = {
        "metadata": {
            "agent_model": agent_data["metadata"].get("model", ""),
            "baseline_model": baseline_data["metadata"].get("model", ""),
            "agent_items": len(agent_items),
            "baseline_items": len(baseline_items),
        },
        "phase1": {"agent": a_p1, "baseline": b_p1},
        "phase2": {"agent": a_p2, "baseline": b_p2},
    }
    OUTPUT_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nResults saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
