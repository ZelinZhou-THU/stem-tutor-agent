"""Per-node time budget management with depth-aware configuration."""
from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NodeBudgetConfig:
    max_wall_seconds: float
    tool_budget_seconds: float
    max_tool_rounds: int
    complexity_timeout_map: dict


DEPTH_PRESETS: dict[str, dict[str, NodeBudgetConfig]] = {
    "no_ref": {
        "reference": NodeBudgetConfig(10, 0, 0, {"simple": 5, "moderate": 8, "complex": 12}),
        "verify": NodeBudgetConfig(120, 30, 2, {"simple": 8, "moderate": 15, "complex": 20}),
        "diagnosis": NodeBudgetConfig(30, 0, 0, {}),
        "feedback": NodeBudgetConfig(30, 0, 0, {}),
        "review": NodeBudgetConfig(30, 0, 0, {}),
    },
    "with_ref": {
        "reference": NodeBudgetConfig(120, 30, 2, {"simple": 8, "moderate": 15, "complex": 20}),
        "verify": NodeBudgetConfig(90, 20, 2, {"simple": 8, "moderate": 15, "complex": 20}),
        "diagnosis": NodeBudgetConfig(30, 0, 0, {}),
        "feedback": NodeBudgetConfig(30, 0, 0, {}),
        "review": NodeBudgetConfig(30, 0, 0, {}),
    },
}

_BUDGET_TIMEOUT_ENV = "STEM_TUTOR_CURRENT_TOOL_TIMEOUT"


def _load_from_preset(depth: str, node_name: str) -> NodeBudgetConfig:
    preset = DEPTH_PRESETS.get(depth, DEPTH_PRESETS["with_ref"])
    return preset.get(node_name, preset.get("reference"))


def load_budget_config(
    depth: str,
    node_name: str,
    subject_overrides: dict | None = None,
) -> NodeBudgetConfig:
    env_depth = os.environ.get("STEM_TUTOR_DEPTH", "").strip().lower()
    effective_depth = env_depth if env_depth in DEPTH_PRESETS else (depth if depth in DEPTH_PRESETS else "with_ref")

    config = _load_from_preset(effective_depth, node_name)

    if subject_overrides and node_name in subject_overrides:
        override = subject_overrides[node_name]
        config = NodeBudgetConfig(
            max_wall_seconds=override.get("max_wall_seconds", config.max_wall_seconds),
            tool_budget_seconds=override.get("tool_budget_seconds", config.tool_budget_seconds),
            max_tool_rounds=override.get("max_tool_rounds", config.max_tool_rounds),
            complexity_timeout_map=config.complexity_timeout_map,
        )

    wall_override = os.environ.get(f"STEM_TUTOR_BUDGET_{node_name.upper()}_WALL", "").strip()
    if wall_override:
        try:
            config = NodeBudgetConfig(
                max_wall_seconds=float(wall_override),
                tool_budget_seconds=config.tool_budget_seconds,
                max_tool_rounds=config.max_tool_rounds,
                complexity_timeout_map=config.complexity_timeout_map,
            )
        except ValueError:
            pass

    return config


def budget_from_global(global_dict: dict, node_name: str) -> NodeBudgetConfig:
    from stem_tutor.graph.global_budget import GlobalBudgetState
    gb = GlobalBudgetState.from_dict(global_dict)
    if node_name == "verify":
        wall = gb.verify_available()
        return NodeBudgetConfig(wall, wall * 0.4, 2, {"simple": 8, "moderate": 15, "complex": 20})
    elif node_name == "reference":
        wall = gb.reference_reserved
        return NodeBudgetConfig(wall, wall * 0.3, 2, {"simple": 8, "moderate": 15, "complex": 20})
    else:
        wall = gb.others_reserved
        return NodeBudgetConfig(wall, 0, 0, {})


class NodeBudgetManager:
    """Manage per-node execution budget.

    Responsibilities:
    1. Track wall-clock elapsed time
    2. Track cumulative tool-call time
    3. Compute dynamic timeout for next tool call
    4. Decide when to skip to fallback strategy
    """

    def __init__(self, config: NodeBudgetConfig, complexity: str = "moderate"):
        self._config = config
        self._complexity = complexity
        self._start = time.perf_counter()
        self._tool_time_used = 0.0
        self._tool_rounds_used = 0

    def elapsed(self) -> float:
        return time.perf_counter() - self._start

    def remaining(self) -> float:
        return max(0.0, self._config.max_wall_seconds - self.elapsed())

    def wall_clock_exceeded(self) -> bool:
        return self.elapsed() >= self._config.max_wall_seconds

    def tool_budget_remaining(self) -> float:
        return max(0.0, self._config.tool_budget_seconds - self._tool_time_used)

    def tool_timeout_for_next_call(self) -> int:
        complexity_timeout = self._config.complexity_timeout_map.get(self._complexity, 10)
        budget_timeout = max(3, int(self.tool_budget_remaining()))
        remaining_rounds = max(1, self._config.max_tool_rounds - self._tool_rounds_used)
        per_round_budget = budget_timeout // remaining_rounds
        return max(per_round_budget, min(complexity_timeout, budget_timeout))

    def can_make_tool_call(self) -> bool:
        return (
            self._tool_rounds_used < self._config.max_tool_rounds
            and self.tool_budget_remaining() > 3
            and self.remaining() > 10
        )

    def record_tool_call(self, elapsed_seconds: float):
        self._tool_time_used += elapsed_seconds
        self._tool_rounds_used += 1

    def should_skip_to_best_effort(self) -> bool:
        return self.remaining() < self._config.max_wall_seconds * 0.2

    @contextmanager
    def tool_execution_context(self):
        timeout = self.tool_timeout_for_next_call()
        old = os.environ.get(_BUDGET_TIMEOUT_ENV)
        os.environ[_BUDGET_TIMEOUT_ENV] = str(timeout)
        try:
            yield timeout
        finally:
            if old is not None:
                os.environ[_BUDGET_TIMEOUT_ENV] = old
            else:
                os.environ.pop(_BUDGET_TIMEOUT_ENV, None)

    def summary(self) -> dict:
        return {
            "elapsed_seconds": round(self.elapsed(), 2),
            "remaining_seconds": round(self.remaining(), 2),
            "tool_time_used": round(self._tool_time_used, 2),
            "tool_rounds_used": self._tool_rounds_used,
            "complexity": self._complexity,
            "wall_limit": self._config.max_wall_seconds,
        }
