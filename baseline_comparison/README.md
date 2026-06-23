# Evaluation Data for Review

## Overview

Two JSON files containing complete evaluation data for 22 problems (10 real homework + 12 planted errors).

| File | Mode | Model |
|------|------|-------|
| `agent_eval_data.json` | workflow_r1 (Agent) | qwen/qwen3.6-plus |
| `baseline_eval_data.json` | baseline_qwen (Single Prompt) | qwen/qwen3.6-plus |

## Quick Start (Reproducible Evaluation)

```bash
cd Stem_Tutor/baseline_comparison
python compute_comparison.py
```

This script:
- Reads `agent_eval_data.json` and `baseline_eval_data.json`
- Computes all Phase 1 (problem-level) and Phase 2 (step-level) metrics
- Prints formatted comparison tables to console
- Saves results to `comparison_results.json`

**Dependencies**: Python 3.10+, `pydantic`, `langchain-core` (only for type imports; no API keys or DB access required).

## Data Source

### Agent
- 22 evaluation items (10 real homework + 12 planted errors)
- Mode: workflow_r1 (multi-step LangGraph workflow)

### Baseline
- 22 evaluation items (10 real homework + 12 planted errors)
- Mode: baseline_qwen (single-prompt baseline)

## JSON Structure

```
{
  "metadata": { mode, model, item counts, export timestamp },
  "phase1_aggregate": { tp, fn, fp, tn, recall, fpr, code_accuracy },
  "phase2_aggregate": { verification_accuracy, error_step_accuracy, ... },
  "items": [
    {
      "seq": <int>,
      "phase": "phase1_real_homework" | "phase2_planted_error",
      "case_id": "<string>",
      "gold_labels": { ... },
      "run_summary": {
        "status": "success" | "failed",
        "steps": [{ step_id, label, raw_text, evidence, confidence }],
        "diagnoses": [{ error_code, category, root_cause_hypothesis, ... }],
        "reference_solution": { ... },
        "review_problems": [{ ... }],
        "final_feedback": { ... },
        "uncertainty_flags": [...],
        "run_meta": { timing, model, node_stats, ... }
      },
      "raw_output": { ... complete run state ... },
      "computed": { per-case metrics }
    }
  ]
}
```

## Aggregate Results

### Phase 1 (Real Homework, Problem-Level)

| Metric | Agent | Baseline |
|--------|-------|----------|
| Detection Recall | 80.0% | 80.0% |
| False Positive Rate | 40.0% | 40.0% |
| Code Accuracy | 20.0% | 60.0% |

### Phase 2 (Planted Errors, Step-Level)

| Metric | Agent | Baseline | Delta |
|--------|-------|----------|-------|
| verification_accuracy | 0.9236 | 0.9375 | -0.0139 |
| lenient_verify_acc | 0.9375 | 0.9375 | +0.0000 |
| error_step_accuracy | 1.0000 | 0.9583 | +0.0417 |
| correct_step_accuracy | 0.9167 | 1.0000 | -0.0833 |
| diagnosis_hit | 0.8333 | 0.9167 | -0.0834 |
| error_step_recall | 0.9792 | 0.9583 | +0.0209 |
| taxonomy_category_hit | 0.9167 | 0.9167 | +0.0000 |
| first_error_hit | 0.8333 | 1.0000 | -0.1667 |
