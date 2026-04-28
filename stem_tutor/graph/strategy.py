"""Universal strategy chain with budget-aware execution."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field as dc_field
from typing import Any, Callable

from stem_tutor.graph.budget import NodeBudgetManager

logger = logging.getLogger(__name__)


@dataclass
class StrategyOutcome:
    data: Any
    quality: str
    confidence: float
    strategy_name: str = ""
    elapsed_seconds: float = 0.0
    metadata: dict = dc_field(default_factory=dict)


class StrategyChain:
    """Execute strategies in priority order, selecting the best result under budget constraints.

    Each strategy is a function with signature:
        fn(budget: NodeBudgetManager, **kwargs) -> StrategyOutcome

    Dispatch rules:
    1. Try each strategy from highest to lowest priority
    2. If quality=="full" and confidence>=0.8, return immediately
    3. If budget low (should_skip_to_best_effort), skip to the last fallback strategy
    4. Always return the best result among all attempted strategies
    """

    def __init__(
        self,
        strategies: list[tuple[str, Callable]],
        budget: NodeBudgetManager,
    ):
        self._strategies = strategies
        self._budget = budget

    def execute(self, **kwargs) -> StrategyOutcome:
        best: StrategyOutcome | None = None
        fallback_name = self._strategies[-1][0] if self._strategies else None

        for name, fn in self._strategies:
            if self._budget.should_skip_to_best_effort() and name != fallback_name:
                logger.info(
                    f"[StrategyChain] Skipping '{name}' due to budget pressure, "
                    f"remaining={self._budget.remaining():.1f}s"
                )
                continue

            if not self._budget.can_make_tool_call() and name != fallback_name:
                if self._budget._config.tool_budget_seconds > 0:
                    logger.info(f"[StrategyChain] Skipping '{name}' - tool budget exhausted")
                    continue

            start = time.perf_counter()
            try:
                result = fn(budget=self._budget, **kwargs)
            except Exception as e:
                elapsed = time.perf_counter() - start
                logger.warning(f"[StrategyChain] Strategy '{name}' failed in {elapsed:.1f}s: {e}")
                continue
            elapsed = time.perf_counter() - start

            if not isinstance(result, StrategyOutcome):
                result = StrategyOutcome(
                    data=result,
                    quality="full",
                    confidence=0.8,
                    strategy_name=name,
                    elapsed_seconds=elapsed,
                )
            else:
                result.strategy_name = name
                result.elapsed_seconds = elapsed

            logger.info(
                f"[StrategyChain] '{name}' -> quality={result.quality}, "
                f"conf={result.confidence:.2f}, elapsed={elapsed:.1f}s"
            )

            if result.quality == "full" and result.confidence >= 0.8:
                return result

            if best is None or result.confidence > best.confidence:
                best = result

            if self._budget.wall_clock_exceeded():
                logger.warning(f"[StrategyChain] Wall clock exceeded after '{name}'")
                break

        return best or StrategyOutcome(None, "failed", 0.0, "none")
