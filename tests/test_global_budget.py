from stem_tutor.graph.global_budget import (
    GlobalBudgetState,
    step_bonus,
    calculate_total_budget,
    DEPTH_TOTAL_BUDGETS,
    VERIFY_RESERVE_FRAC,
    REFERENCE_RESERVE_FRAC,
    OTHERS_RESERVE_FRAC,
)


def test_step_bonus_1step():
    assert step_bonus(1) == 10.0


def test_step_bonus_3steps():
    assert step_bonus(3) == 30.0


def test_step_bonus_5steps():
    assert step_bonus(5) == 54.0


def test_step_bonus_8steps():
    assert step_bonus(8) == 96.0


def test_step_bonus_12steps():
    assert step_bonus(12) == 166.0


def test_total_budget_standard_2steps():
    assert calculate_total_budget("standard", 2) == 360.0 + 20.0


def test_total_budget_quick_5steps():
    assert calculate_total_budget("quick", 5) == 198.0 + 54.0


def test_verify_reserve_fraction():
    gb = GlobalBudgetState.create("standard", 3)
    assert abs(gb.verify_reserved - gb.total_budget * VERIFY_RESERVE_FRAC) < 1e-6


def test_verify_available_no_overflow():
    gb = GlobalBudgetState.create("standard", 3)
    assert abs(gb.verify_available() - gb.verify_reserved) < 1e-6


def test_verify_available_with_overflow():
    gb = GlobalBudgetState.create("standard", 3)
    gb.reference_used = gb.reference_reserved + 30.0
    expected_borrow = min(30.0, gb.others_reserved * 0.5)
    expected = gb.verify_reserved + expected_borrow
    assert abs(gb.verify_available() - expected) < 1e-6


def test_critical_mode_below_30():
    gb = GlobalBudgetState.create("quick", 2)
    gb.verify_used = gb.verify_reserved - 10.0
    assert gb.critical_mode() is True


def test_critical_mode_above_30():
    gb = GlobalBudgetState.create("thorough", 2)
    assert gb.critical_mode() is False


def test_per_step_budget():
    gb = GlobalBudgetState.create("standard", 5)
    expected = gb.verify_available() / 5
    assert abs(gb.per_step_budget() - expected) < 1e-6


def test_serialization_roundtrip():
    gb = GlobalBudgetState.create("standard", 5)
    d = gb.to_dict()
    gb2 = GlobalBudgetState.from_dict(d)
    d2 = gb2.to_dict()
    assert d == d2


def test_create_factory():
    gb = GlobalBudgetState.create("standard", 5)
    expected_total = 360.0 + 54.0
    assert abs(gb.total_budget - expected_total) < 1e-6
    assert gb.step_count == 5
    assert gb.depth == "standard"
