from __future__ import annotations

import argparse
import json
from pathlib import Path

from stem_tutor.evaluation.runner import evaluate_cases
from stem_tutor.providers.factory import create_provider
from stem_tutor.settings import load_provider_settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="STEM Tutor evaluation runner")
    parser.add_argument(
        "--cases",
        default="fixtures/eval_cases.json",
        help="Path to evaluation cases JSON file",
    )
    parser.add_argument(
        "--provider",
        default="mock",
        choices=["mock", "real"],
        help="Provider backend",
    )
    parser.add_argument(
        "--mode",
        default="workflow_r1",
        choices=["workflow_r1", "baseline_glm5", "baseline_kimi"],
        help="Evaluation mode",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional path to save evaluation JSON result",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_provider_settings()
    baseline_name = None
    model_group = "reasoning"
    if args.mode == "baseline_glm5":
        model_group = "baseline"
        baseline_name = "glm5"
    elif args.mode == "baseline_kimi":
        model_group = "baseline"
        baseline_name = "kimi"

    provider = create_provider(
        args.provider,
        settings,
        model_group=model_group,
        baseline_name=baseline_name,
    )

    result = evaluate_cases(provider, Path(args.cases), mode=args.mode)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
