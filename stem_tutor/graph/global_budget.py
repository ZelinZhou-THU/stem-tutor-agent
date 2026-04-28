"""全局预算管理：跨节点的统一预算分配与动态调整。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

DEPTH_TOTAL_BUDGETS = {"quick": 198.0, "standard": 360.0, "thorough": 565.0}

VERIFY_RESERVE_FRAC = 0.45
REFERENCE_RESERVE_FRAC = 0.35
OTHERS_RESERVE_FRAC = 0.20


def step_bonus(step_count: int) -> float:
    if step_count <= 3:
        return step_count * 10.0
    if step_count <= 6:
        return 30.0 + (step_count - 3) * 12.0
    if step_count <= 10:
        return 66.0 + (step_count - 6) * 15.0
    return 126.0 + (step_count - 10) * 20.0


def calculate_total_budget(depth: str, step_count: int) -> float:
    base = DEPTH_TOTAL_BUDGETS.get(depth, DEPTH_TOTAL_BUDGETS["standard"])
    return base + step_bonus(step_count)


@dataclass
class GlobalBudgetState:
    total_budget: float
    verify_reserved: float
    reference_reserved: float
    others_reserved: float
    step_count: int
    reference_used: float = 0.0
    verify_used: float = 0.0
    depth: str = "standard"

    def verify_available(self) -> float:
        pool = self.verify_reserved
        if self.reference_used > self.reference_reserved:
            overflow = self.reference_used - self.reference_reserved
            borrow = min(overflow, self.others_reserved * 0.5)
            pool += borrow
        return max(0.0, pool - self.verify_used)

    def critical_mode(self) -> bool:
        return self.verify_available() < 30.0

    def per_step_budget(self) -> float:
        return self.verify_available() / max(1, self.step_count)

    def to_dict(self) -> dict:
        return {
            "total_budget": self.total_budget,
            "verify_reserved": self.verify_reserved,
            "reference_reserved": self.reference_reserved,
            "others_reserved": self.others_reserved,
            "step_count": self.step_count,
            "reference_used": self.reference_used,
            "verify_used": self.verify_used,
            "depth": self.depth,
        }

    @classmethod
    def from_dict(cls, d: dict) -> GlobalBudgetState:
        return cls(**{k: d.get(k, 0) for k in [
            "total_budget", "verify_reserved", "reference_reserved",
            "others_reserved", "step_count", "reference_used",
            "verify_used", "depth",
        ]})

    @classmethod
    def create(cls, depth: str, step_count: int) -> GlobalBudgetState:
        total = calculate_total_budget(depth, step_count)
        return cls(
            total_budget=total,
            verify_reserved=total * VERIFY_RESERVE_FRAC,
            reference_reserved=total * REFERENCE_RESERVE_FRAC,
            others_reserved=total * OTHERS_RESERVE_FRAC,
            step_count=step_count,
            depth=depth,
        )
