"""Unit tests for stem_tutor.sympy_verify module."""
from __future__ import annotations

import pytest

from stem_tutor.sympy_verify import (
    extract_expressions,
    safe_parse_expr,
    sympy_verify_step,
)


class TestExtractExpressions:
    def test_single_equation(self):
        assert extract_expressions("x**2 + 2*x + 1 = (x+1)**2") == ["x**2 + 2*x + 1", "(x+1)**2"]

    def test_no_equals(self):
        result = extract_expressions("sin(x**2)")
        assert len(result) == 1
        assert "sin" in result[0]

    def test_chinese_prefix(self):
        result = extract_expressions("解：x**2 = 4")
        assert result == ["x**2", "4"]

    def test_therefore_prefix(self):
        result = extract_expressions("therefore x = 2")
        assert result == ["x", "2"]

    def test_empty(self):
        assert extract_expressions("") == []
        assert extract_expressions("   ") == []


class TestSafeParseExpr:
    def test_polynomial(self):
        expr = safe_parse_expr("x**2 + 2*x + 1")
        assert expr is not None

    def test_trig(self):
        expr = safe_parse_expr("sin(x)")
        assert expr is not None

    def test_sqrt(self):
        expr = safe_parse_expr("sqrt(x)")
        assert expr is not None

    def test_plain_text_returns_none(self):
        assert safe_parse_expr("therefore done") is None
        assert safe_parse_expr("hello world") is None

    def test_single_symbol_returns_none(self):
        assert safe_parse_expr("x") is None
        assert safe_parse_expr("t") is None

    def test_empty(self):
        assert safe_parse_expr("") is None


class TestSympyVerifyStep:
    def test_correct_derivative(self):
        result = sympy_verify_step("d/dx[sin(x**2)] = 2*x*cos(x**2)")
        assert result is True

    def test_wrong_derivative(self):
        result = sympy_verify_step("d/dx[sin(x**2)] = cos(x**2)")
        assert result is False

    def test_algebraic_equivalence(self):
        result = sympy_verify_step("(x+1)**2 = x**2 + 2*x + 1")
        assert result is True

    def test_trig_identity(self):
        result = sympy_verify_step("sin(x)**2 + cos(x)**2 = 1")
        assert result is True

    def test_unequal_expressions(self):
        result = sympy_verify_step("x**2 = x**3")
        assert result is False

    def test_plain_text_returns_none(self):
        result = sympy_verify_step("therefore done")
        assert result is None

    def test_mixed_text_returns_none(self):
        result = sympy_verify_step("令 u = x**2，则 du = 2*x dx")
        assert result is None

    def test_empty_returns_none(self):
        result = sympy_verify_step("")
        assert result is None

    def test_simple_equality(self):
        result = sympy_verify_step("2 + 2 = 4")
        assert result is True

    def test_simple_inequality(self):
        result = sympy_verify_step("2 + 2 = 5")
        assert result is False

    def test_integral_basic(self):
        result = sympy_verify_step("integrate(2*x, x) = x**2")
        assert result is True

    def test_prev_context_match(self):
        result = sympy_verify_step(
            step_text="x**2 + 2*x + 1",
            prev_text="(x+1)**2",
        )
        assert result is True

    def test_prev_context_no_match(self):
        result = sympy_verify_step(
            step_text="x**3",
            prev_text="x**2",
        )
        assert result is None
