"""Export complete eval data for teacher review.

Outputs two self-contained JSON files (agent + baseline), each with:
- 22 items (10 Phase 1 real homework + 12 Phase 2 planted errors)
- Gold labels
- Complete run data (raw_output with all intermediate results)
- Per-case computed metrics
- Aggregate statistics
"""
import json
import sqlite3
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, ".")

from stem_tutor.evaluation.runner import (
    _verification_accuracy,
    _error_step_accuracy,
    _correct_step_accuracy,
    _lenient_verification_accuracy,
    _diagnosis_hit,
    _first_error_step,
    _error_step_recall,
    _taxonomy_category_hit,
    _normalize_items,
)

DB_PATH = "data/stem_tutor.db"
GOLD_PATH = "../TestData4StemTutor/eval_output/combined_gold_labels.json"
OUTPUT_DIR = Path("../TestData4StemTutor/eval_output/for_review")

WF_V2 = "eee63456-bf1e-4eca-a258-dfd3d5615e76"
MINI = "729d34ab-4d4a-43ca-b18f-053f9c2bd327"
BL = "24bf772b-90c3-4542-a4bb-b0a5eea995dc"
MINI_SEQ_MAP = {0: 15, 1: 17, 2: 18, 3: 19}

_NON_CORRECT = {"incorrect_math", "inconsistent_or_unsupported", "unclear"}

METRIC_KEYS = [
    "verification_accuracy",
    "lenient_verify_acc",
    "error_step_accuracy",
    "correct_step_accuracy",
    "diagnosis_hit",
    "error_step_recall",
    "taxonomy_category_hit",
    "first_error_hit",
]


def load_run(conn, run_id):
    if not run_id:
        return None
    row = conn.execute("SELECT data FROM runs WHERE id=?", (run_id,)).fetchone()
    if not row or not row["data"]:
        return None
    return json.loads(row["data"])


def load_batch(conn, batch_id):
    items = conn.execute(
        "SELECT seq, run_id FROM batch_items WHERE batch_id=? ORDER BY seq",
        (batch_id,),
    ).fetchall()
    return {item["seq"]: load_run(conn, item["run_id"]) for item in items}


def extract_vr_dr(run_data):
    if not run_data:
        return [], []
    raw = run_data.get("raw_output", {})
    if raw and isinstance(raw, dict) and raw.get("verification_results"):
        vr = _normalize_items(raw.get("verification_results", []))
        dr = _normalize_items(raw.get("diagnosis_results", []))
        return vr, dr
    steps = run_data.get("steps", [])
    vr = [
        {
            "step_id": s.get("step_id", ""),
            "label": s.get("label", ""),
            "raw_text": s.get("raw_text", ""),
            "evidence": s.get("evidence", ""),
            "confidence": s.get("confidence", 0),
        }
        for s in steps
    ] if steps else []
    dr = run_data.get("diagnoses", [])
    return vr, dr


def compute_phase2_metrics(run_data, gold_case):
    vr, dr = extract_vr_dr(run_data)
    gv = gold_case.get("gold_verification", [])
    gc = gold_case.get("gold_diagnosis_codes", [])
    ef = gold_case.get("gold_first_error_step")
    pred_first = _first_error_step(vr)
    return {
        "verification_accuracy": round(_verification_accuracy(vr, gv), 4),
        "lenient_verify_acc": round(_lenient_verification_accuracy(vr, gv), 4),
        "error_step_accuracy": round(_error_step_accuracy(vr, gv), 4),
        "correct_step_accuracy": round(_correct_step_accuracy(vr, gv), 4),
        "diagnosis_hit": round(_diagnosis_hit(dr, gc), 4),
        "error_step_recall": round(_error_step_recall(vr, gv), 4),
        "taxonomy_category_hit": round(_taxonomy_category_hit(dr, gc), 4),
        "first_error_hit": round(1.0 if ef == pred_first else 0.0, 4),
        "predicted_first_error_step": pred_first,
    }


def compute_phase1_metrics(run_data, gold_case):
    vr, dr = extract_vr_dr(run_data)
    agent_detected = any(v.get("label") in _NON_CORRECT for v in vr)
    agent_codes = sorted({d.get("error_code", "") for d in dr if d.get("error_code")})
    gold_code = gold_case.get("gold_error_code", "")
    gold_is_error = not gold_case.get("gold_is_correct", True)
    code_match = bool(gold_code) and gold_code in set(agent_codes) if gold_code else False

    if gold_is_error and agent_detected:
        cls = "TP"
    elif gold_is_error and not agent_detected:
        cls = "FN"
    elif not gold_is_error and agent_detected:
        cls = "FP"
    else:
        cls = "TN"

    return {
        "classification": cls,
        "detected": agent_detected,
        "agent_diagnosis_codes": agent_codes,
        "error_code_match": code_match,
    }


def build_export(conn, runs, gold_data, mode_label):
    p1_gold = gold_data["phase1"]
    p2_gold = gold_data["phase2"]

    items = []

    for g in p1_gold:
        seq = g["batch_seq"]
        run_data = runs.get(seq)
        vr, dr = extract_vr_dr(run_data)
        raw_output = run_data.get("raw_output", {}) if run_data else {}

        item = {
            "seq": seq,
            "phase": "phase1_real_homework",
            "case_id": g.get("id", "?"),
            "gold_labels": {
                "is_correct": g.get("gold_is_correct", True),
                "error_type_raw": g.get("gold_error_type_raw", ""),
                "error_code": g.get("gold_error_code", ""),
                "error_category": g.get("gold_error_category", ""),
                "error_explanation": g.get("gold_error_explanation", ""),
                "reference": g.get("gold_reference", ""),
                "label_source": g.get("gold_label_source", ""),
            },
            "run_summary": {
                "status": run_data.get("status", "unknown") if run_data else "missing",
                "steps": vr,
                "diagnoses": dr,
                "reference_solution": raw_output.get("reference_solution"),
                "review_problems": raw_output.get("review_problems", []),
                "final_feedback": raw_output.get("final_feedback", {}),
                "uncertainty_flags": raw_output.get("uncertainty_flags", []),
                "run_meta": raw_output.get("run_meta", {}),
            },
            "raw_output": raw_output,
            "computed": compute_phase1_metrics(run_data, g),
        }
        items.append(item)

    for g in p2_gold:
        seq = g["batch_seq"]
        run_data = runs.get(seq)
        vr, dr = extract_vr_dr(run_data)
        raw_output = run_data.get("raw_output", {}) if run_data else {}

        item = {
            "seq": seq,
            "phase": "phase2_planted_error",
            "case_id": g.get("case_id", "?"),
            "gold_labels": {
                "gold_verification": g.get("gold_verification", []),
                "gold_first_error_step": g.get("gold_first_error_step"),
                "gold_diagnosis_codes": g.get("gold_diagnosis_codes", []),
            },
            "run_summary": {
                "status": run_data.get("status", "unknown") if run_data else "missing",
                "steps": vr,
                "diagnoses": dr,
                "reference_solution": raw_output.get("reference_solution"),
                "review_problems": raw_output.get("review_problems", []),
                "final_feedback": raw_output.get("final_feedback", {}),
                "uncertainty_flags": raw_output.get("uncertainty_flags", []),
                "run_meta": raw_output.get("run_meta", {}),
            },
            "raw_output": raw_output,
            "computed": compute_phase2_metrics(run_data, g),
        }
        items.append(item)

    # Aggregate
    p1_items = [i for i in items if i["phase"].startswith("phase1")]
    p2_items = [i for i in items if i["phase"].startswith("phase2")]

    tp = sum(1 for i in p1_items if i["computed"]["classification"] == "TP")
    fn = sum(1 for i in p1_items if i["computed"]["classification"] == "FN")
    fp = sum(1 for i in p1_items if i["computed"]["classification"] == "FP")
    tn = sum(1 for i in p1_items if i["computed"]["classification"] == "TN")
    error_cases = [i for i in p1_items if i["computed"]["classification"] in ("TP", "FN")]
    code_matches = sum(1 for i in error_cases if i["computed"]["error_code_match"])

    p1_agg = {
        "tp": tp,
        "fn": fn,
        "fp": fp,
        "tn": tn,
        "detection_recall": round(tp / (tp + fn), 4) if (tp + fn) else 0,
        "false_positive_rate": round(fp / (fp + tn), 4) if (fp + tn) else 0,
        "code_accuracy": round(code_matches / len(error_cases), 4) if error_cases else 0,
    }

    n = len(p2_items)
    p2_agg = {
        k: round(sum(i["computed"].get(k, 0) for i in p2_items) / n, 4) if n else 0
        for k in METRIC_KEYS
    }

    return {
        "metadata": {
            "mode": mode_label,
            "model": "qwen/qwen3.6-plus",
            "total_items": len(items),
            "phase1_items": len(p1_items),
            "phase2_items": len(p2_items),
            "exported_at": datetime.now().isoformat(),
        },
        "phase1_aggregate": p1_agg,
        "phase2_aggregate": p2_agg,
        "items": items,
    }


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    gold = json.loads(Path(GOLD_PATH).read_text(encoding="utf-8"))

    print("Loading batches...")
    v2_runs = load_batch(conn, WF_V2)
    mini_runs = load_batch(conn, MINI)
    bl_runs = load_batch(conn, BL)
    print(f"  v2: {len(v2_runs)} runs")
    print(f"  mini: {len(mini_runs)} runs")
    print(f"  baseline: {len(bl_runs)} runs")

    # Build patched agent runs
    agent_runs = dict(v2_runs)
    for mini_seq, orig_seq in MINI_SEQ_MAP.items():
        if mini_seq in mini_runs:
            agent_runs[orig_seq] = mini_runs[mini_seq]
            print(f"  Patched seq {orig_seq} <- mini seq {mini_seq}")

    # Export
    print("\nBuilding agent export...")
    agent_export = build_export(conn, agent_runs, gold, "workflow_r1 (patched v2+mini)")

    print("Building baseline export...")
    baseline_export = build_export(conn, bl_runs, gold, "baseline_qwen")

    # Write files
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    agent_path = OUTPUT_DIR / "agent_eval_data.json"
    baseline_path = OUTPUT_DIR / "baseline_eval_data.json"

    agent_path.write_text(
        json.dumps(agent_export, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    baseline_path.write_text(
        json.dumps(baseline_export, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\nAgent data: {agent_path} ({agent_path.stat().st_size:,} bytes)")
    print(f"Baseline data: {baseline_path} ({baseline_path.stat().st_size:,} bytes)")

    # Write README
    readme = f"""# Evaluation Data for Review

## Overview

Two JSON files containing complete evaluation data for 22 problems (10 real homework + 12 planted errors).

| File | Mode | Model |
|------|------|-------|
| `agent_eval_data.json` | workflow_r1 (Agent, patched) | qwen/qwen3.6-plus |
| `baseline_eval_data.json` | baseline_qwen (Single Prompt) | qwen/qwen3.6-plus |

## Data Source

### Agent (patched)
- Base batch: `{WF_V2}` (22 items, run after substitution rule fix)
- Override batch: `{MINI}` (4 items, re-run after reverting +C check rule)
- Override mapping: mini seq {{0,1,2,3}} -> original seq {{15,17,18,19}}
  = planted-006, planted-008, planted-009, planted-010

### Baseline
- Batch: `{BL}` (22 items, single-prompt baseline)

## JSON Structure

```
{{
  "metadata": {{ mode, model, item counts, export timestamp }},
  "phase1_aggregate": {{ tp, fn, fp, tn, recall, fpr, code_accuracy }},
  "phase2_aggregate": {{ verification_accuracy, error_step_accuracy, ... }},
  "items": [
    {{
      "seq": <int>,
      "phase": "phase1_real_homework" | "phase2_planted_error",
      "case_id": "<string>",
      "gold_labels": {{ ... }},
      "run_summary": {{
        "status": "success" | "failed",
        "steps": [{{ step_id, label, raw_text, evidence, confidence }}],
        "diagnoses": [{{ error_code, category, root_cause_hypothesis, ... }}],
        "reference_solution": {{ ... }},
        "review_problems": [{{ ... }}],
        "final_feedback": {{ ... }},
        "uncertainty_flags": [...],
        "run_meta": {{ timing, model, node_stats, ... }}
      }},
      "raw_output": {{ ... complete run state ... }},
      "computed": {{ per-case metrics }}
    }}
  ]
}}
```

## Aggregate Results

### Phase 1 (Real Homework, Problem-Level)

| Metric | Agent | Baseline |
|--------|-------|----------|
| Detection Recall | {agent_export['phase1_aggregate']['detection_recall']:.1%} | {baseline_export['phase1_aggregate']['detection_recall']:.1%} |
| False Positive Rate | {agent_export['phase1_aggregate']['false_positive_rate']:.1%} | {baseline_export['phase1_aggregate']['false_positive_rate']:.1%} |
| Code Accuracy | {agent_export['phase1_aggregate']['code_accuracy']:.1%} | {baseline_export['phase1_aggregate']['code_accuracy']:.1%} |

### Phase 2 (Planted Errors, Step-Level)

| Metric | Agent | Baseline | Delta |
|--------|-------|----------|-------|
"""

    for key in METRIC_KEYS:
        a = agent_export["phase2_aggregate"].get(key, 0)
        b = baseline_export["phase2_aggregate"].get(key, 0)
        d = a - b
        sign = "+" if d >= 0 else ""
        readme += f"| {key} | {a:.4f} | {b:.4f} | {sign}{d:.4f} |\n"

    readme_path = OUTPUT_DIR / "README.md"
    readme_path.write_text(readme, encoding="utf-8")
    print(f"README: {readme_path}")

    # Print summary table
    print("\n" + "=" * 70)
    print("  Phase 2 Step-Level Comparison")
    print("=" * 70)
    print(f"  {'Metric':<28} {'Agent':>10} {'Baseline':>10} {'Delta':>8}")
    print("-" * 60)
    for key in METRIC_KEYS:
        a = agent_export["phase2_aggregate"].get(key, 0)
        b = baseline_export["phase2_aggregate"].get(key, 0)
        d = a - b
        sign = "+" if d >= 0 else ""
        flag = " <<<" if a > b else ""
        print(f"  {key:<28} {a:>10.4f} {b:>10.4f} {sign}{d:>7.4f}{flag}")
    print("=" * 70)

    conn.close()


if __name__ == "__main__":
    main()
