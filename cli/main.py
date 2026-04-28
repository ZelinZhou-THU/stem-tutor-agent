from __future__ import annotations

import argparse
import json
from pathlib import Path

from stem_tutor.domain.models import ProblemInput
from stem_tutor.graph.workflow import run_tutor_graph
from stem_tutor.providers.factory import create_provider
from stem_tutor.settings import load_provider_settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="STEM Tutor local demo")
    parser.add_argument("--input", required=True, help="Path to JSON input file")
    parser.add_argument(
        "--provider",
        default="mock",
        choices=["mock", "real"],
        help="Provider backend",
    )
    parser.add_argument(
        "--health-check",
        action="store_true",
        help="Check provider connectivity before running graph",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))

    problem_input = ProblemInput(**payload["problem_input"])
    raw_student_solution = payload["raw_student_solution"]

    settings = load_provider_settings()
    provider = create_provider(args.provider, settings, model_group="reasoning")
    ocr_provider = create_provider(args.provider, settings, model_group="ocr")

    if args.health_check:
        ok, detail = provider.health_check()
        if not ok:
            raise RuntimeError(f"Provider health check failed: {detail}")
        if problem_input.source_type == "ocr":
            ok_ocr, detail_ocr = ocr_provider.health_check()
            if not ok_ocr:
                raise RuntimeError(f"OCR provider health check failed: {detail_ocr}")

    output = run_tutor_graph(provider, problem_input, raw_student_solution, ocr_provider=ocr_provider)

    print(json.dumps(serialize_output(output), ensure_ascii=False, indent=2))


def serialize_output(output: dict) -> dict:
    def maybe_dump(v):
        if hasattr(v, "model_dump"):
            return v.model_dump()
        if isinstance(v, list):
            return [maybe_dump(i) for i in v]
        return v

    return {k: maybe_dump(v) for k, v in output.items()}


if __name__ == "__main__":
    main()
