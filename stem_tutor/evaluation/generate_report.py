"""Generate comprehensive evaluation report."""

import argparse
import json
from pathlib import Path


def generate_report(phase1_path: Path | None, phase2_path: Path | None, output_path: Path) -> str:
    phase1 = _load_json(phase1_path) if phase1_path else {}
    phase2 = _load_json(phase2_path) if phase2_path else {}

    lines = []
    lines.append("# STEM Tutor Agent Evaluation Report\n\n")
    lines.append("> Auto-generated. Data: real student homework + planted error comparison.\n\n")

    if phase1:
        lines.append("## Phase 1: Real Homework Evaluation\n\n")
        lines.append("Data: Calculus A(1)/A(2) student homework, 5 error cases + 5 correct controls.\n")
        lines.append("Mode: Web batch queue, Qwen 3.6 Plus, depth=with_ref.\n\n")

        m = phase1
        lines.append("### Aggregate Metrics\n\n")
        lines.append("| Metric | Value |\n|------|----|\n")
        lines.append(f"| Total cases | {m.get('total_cases', 'N/A')} |\n")
        lines.append(f"| TP (error detected) | {m.get('tp', 'N/A')} |\n")
        lines.append(f"| FN (error missed) | {m.get('fn', 'N/A')} |\n")
        lines.append(f"| FP (false alarm) | {m.get('fp', 'N/A')} |\n")
        lines.append(f"| TN (correct confirmed) | {m.get('tn', 'N/A')} |\n")
        lines.append(f"| **Detection Recall** | **{m.get('detection_recall', 0):.1%}** |\n")
        lines.append(f"| **False Positive Rate** | **{m.get('false_positive_rate', 0):.1%}** |\n")
        lines.append(f"| Taxonomy Code Accuracy | {m.get('code_accuracy', 0):.1%} |\n")
        lines.append(f"| Category Accuracy | {m.get('category_accuracy', 0):.1%} |\n\n")

        lines.append("### Per-Case Details\n\n")
        lines.append("| Class | Case | Agent Codes | First Err Step | Gold Explanation |\n")
        lines.append("|------|------|------------|---------------|-----------------|\n")
        for c in m.get("case_details", []):
            codes = ", ".join(c.get("agent_diagnosis_codes", [])) or "-"
            step = c.get("agent_first_error_step_id") or "-"
            explain = (c.get("gold_error_explanation", "") or "")[:40].replace("|", "\\|")
            lines.append(f"| {c['classification']} | {c['id'][:25]} | {codes} | {step} | {explain} |\n")
        lines.append("\n")

    if phase2:
        lines.append("## Phase 2: Planted Error Comparison (Agent vs Baseline)\n\n")
        lines.append("Data: 12 synthetic calculus problems (10 with specific errors + 2 correct controls).\n")
        lines.append(f"Model: {phase2.get('model', 'N/A')} (same for both modes).\n\n")

        w = phase2.get("workflow", {})
        b = phase2.get("baseline", {})
        lines.append("### Quantitative Comparison\n\n")
        lines.append("| Metric | Agent | Baseline | Delta |\n")
        lines.append("|------|-------|----------|------|\n")
        labels = {
            "avg_verification_accuracy": "Step Verification Accuracy",
            "avg_diagnosis_hit": "Diagnosis Hit Rate",
            "avg_error_step_recall": "Error Step Recall",
            "avg_taxonomy_category_hit": "Taxonomy Category Hit",
            "avg_first_error_hit": "First Error Step Hit",
            "avg_feedback_proxy": "Feedback Quality Proxy",
            "avg_review_relevance_proxy": "Review Relevance",
            "avg_low_conf_trigger_rate": "Low-Confidence Trigger Rate",
            "avg_real_provider_failure_rate": "Provider Failure Rate",
        }
        for key, label in labels.items():
            wv = w.get(key, 0)
            bv = b.get(key, 0)
            delta = wv - bv
            sign = "+" if delta >= 0 else ""
            lines.append(f"| {label} | {wv:.4f} | {bv:.4f} | {sign}{delta:.4f} |\n")
        lines.append(f"| Time (s) | {phase2.get('workflow_time_seconds', 'N/A')} | {phase2.get('baseline_time_seconds', 'N/A')} | - |\n\n")

    lines.append("## Conclusions\n\n")
    lines.append("(To be supplemented with analysis after data collection)\n\n")

    report = "".join(lines)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    return report


def _load_json(path: Path) -> dict:
    if path and path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def main():
    parser = argparse.ArgumentParser(description="Generate evaluation report")
    parser.add_argument("--phase1", type=Path, default=None)
    parser.add_argument("--phase2", type=Path, default=None)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    report = generate_report(args.phase1, args.phase2, args.output)
    print(f"Report saved to {args.output} ({len(report)} chars)")


if __name__ == "__main__":
    main()
