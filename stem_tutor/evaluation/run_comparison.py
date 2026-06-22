"""Agent vs Baseline comparison using same model."""

import argparse
import json
import time
from pathlib import Path

from stem_tutor.evaluation.runner import evaluate_cases
from stem_tutor.providers.factory import create_provider
from stem_tutor.settings import load_provider_settings


def run_comparison(cases_file: Path, output_dir: Path) -> dict:
    settings = load_provider_settings()
    provider = create_provider("openai-compatible", settings, model_group="reasoning")
    model_name = provider.provider_info().get("model_name", "unknown")
    print(f"Provider: {model_name}")
    print(f"Cases: {cases_file}")

    print("\n" + "=" * 60)
    print("Running workflow_r1 (Agent) ...")
    print("=" * 60)
    t0 = time.time()
    workflow_result = evaluate_cases(provider, cases_file, mode="workflow_r1")
    workflow_time = time.time() - t0
    print(f"\nAgent done in {workflow_time:.0f}s")

    print("\n" + "=" * 60)
    print("Running baseline_qwen (Single Prompt) ...")
    print("=" * 60)
    t0 = time.time()
    baseline_result = evaluate_cases(provider, cases_file, mode="baseline_qwen")
    baseline_time = time.time() - t0
    print(f"\nBaseline done in {baseline_time:.0f}s")

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "workflow_result.json").write_text(
        json.dumps(workflow_result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "baseline_result.json").write_text(
        json.dumps(baseline_result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    metrics_keys = [
        "avg_verification_accuracy",
        "avg_diagnosis_hit",
        "avg_error_step_recall",
        "avg_taxonomy_category_hit",
        "avg_first_error_hit",
        "avg_feedback_proxy",
        "avg_review_relevance_proxy",
        "avg_low_conf_trigger_rate",
        "avg_real_provider_failure_rate",
    ]
    comparison = {
        "model": model_name,
        "num_cases": workflow_result["num_cases"],
        "workflow_time_seconds": round(workflow_time, 1),
        "baseline_time_seconds": round(baseline_time, 1),
        "workflow": {k: workflow_result.get(k) for k in metrics_keys},
        "baseline": {k: baseline_result.get(k) for k in metrics_keys},
        "workflow_failure_reasons": workflow_result.get("real_failure_reasons", {}),
        "baseline_failure_reasons": baseline_result.get("real_failure_reasons", {}),
    }
    summary_path = output_dir / "comparison_summary.json"
    summary_path.write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    _print_comparison_table(comparison)
    return comparison


def _print_comparison_table(c: dict) -> None:
    w = c["workflow"]
    b = c["baseline"]
    print("\n" + "=" * 70)
    print(f"  COMPARISON: Agent (workflow_r1) vs Baseline (baseline_qwen)")
    print(f"  Model: {c['model']} | Cases: {c['num_cases']}")
    print("=" * 70)
    print(f"  {'Metric':<40} {'Agent':>10} {'Baseline':>10} {'Delta':>8}")
    print("-" * 70)
    labels = {
        "avg_verification_accuracy": "Verification Accuracy",
        "avg_diagnosis_hit": "Diagnosis Hit",
        "avg_error_step_recall": "Error Step Recall",
        "avg_taxonomy_category_hit": "Taxonomy Category Hit",
        "avg_first_error_hit": "First Error Step Hit",
        "avg_feedback_proxy": "Feedback Proxy",
        "avg_review_relevance_proxy": "Review Relevance",
        "avg_low_conf_trigger_rate": "Low-Conf Trigger Rate",
        "avg_real_provider_failure_rate": "Provider Failure Rate",
    }
    for key in labels:
        wv = w.get(key, 0)
        bv = b.get(key, 0)
        delta = wv - bv
        sign = "+" if delta >= 0 else ""
        print(f"  {labels[key]:<40} {wv:>10.4f} {bv:>10.4f} {sign}{delta:>7.4f}")
    print("-" * 70)
    print(f"  {'Time (seconds)':<40} {c['workflow_time_seconds']:>10.1f} {c['baseline_time_seconds']:>10.1f}")
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Agent vs Baseline comparison")
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    run_comparison(args.cases, args.output)
    print(f"\nResults saved to {args.output}")


if __name__ == "__main__":
    main()
