from __future__ import annotations

import os
import re

from stem_tutor.domain.models import SolutionStep
from stem_tutor.graph.state import TutorGraphState
from stem_tutor.providers.base import LLMProvider


def _rule_based_parse(raw: str) -> tuple[list[SolutionStep], list[str]]:
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if not lines:
        return [], ["empty_input"]

    merged: list[str] = []
    for line in lines:
        if line.startswith("=") and merged:
            merged[-1] = merged[-1] + " " + line
        else:
            merged.append(line)

    steps: list[SolutionStep] = []
    warnings: list[str] = []
    for i, text in enumerate(merged, start=1):
        normalized = re.sub(r"\s+", " ", text)
        if len(normalized) < 3:
            warnings.append(f"step text may be too short: {normalized[:30]}")
            continue
        steps.append(SolutionStep(step_id=f"S{i}", raw_text=normalized, normalized_text=normalized))

    return steps, warnings


def make_parse_student_solution_node(provider: LLMProvider):
    def parse_student_solution_node(state: TutorGraphState) -> TutorGraphState:
        raw = state.get("raw_student_solution", "")
        steps: list[SolutionStep] = []
        warnings: list[str] = []

        llm_steps = provider.parse_steps(raw)

        if llm_steps and isinstance(llm_steps, list) and len(llm_steps) > 0:
            for i, s in enumerate(llm_steps, start=1):
                text = str(s.get("text", "")).strip()
                if not text:
                    continue
                normalized = re.sub(r"\s+", " ", text)
                steps.append(SolutionStep(step_id=f"S{i}", raw_text=normalized, normalized_text=normalized))
            trace_msg = f"parse_student_solution: LLM parsed {len(steps)} steps"
        else:
            steps, warnings = _rule_based_parse(raw)
            trace_msg = f"parse_student_solution: LLM failed, rule-based parsed {len(steps)} steps"
            warnings.insert(0, "llm_parse_failed_used_rule_fallback")

        if not steps:
            warnings.append("no_parseable_steps")

        trace = state.get("trace", [])
        trace.append(trace_msg)

        new_state: TutorGraphState = {
            "normalized_steps": steps,
            "parse_warnings": warnings,
            "trace": trace,
        }

        if not steps:
            new_state["fail_reason"] = "No parseable student steps"

        gb_dict = None
        if os.environ.get("STEM_TUTOR_BUDGET_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}:
            from stem_tutor.graph.global_budget import GlobalBudgetState
            depth = state.get("budget_metadata", {}).get("depth", "standard")
            gb = GlobalBudgetState.create(depth, len(steps))
            gb_dict = gb.to_dict()

        new_state["global_budget"] = gb_dict

        return new_state

    return parse_student_solution_node


parse_student_solution_node = make_parse_student_solution_node
