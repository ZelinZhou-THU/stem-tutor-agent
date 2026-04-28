"""Integration tests for SymPy verification with real calculus steps."""
from __future__ import annotations

import pytest

from stem_tutor.sympy_verify import sympy_verify_step


class TestRealCalculusSteps:
    """Test with actual student solution steps from the Euler integral problem."""

    def test_step1_substitution_form(self):
        """S1: Integral form with differential notation."""
        result = sympy_verify_step(
            r"\int_0^1 \frac{dx}{\sqrt{1-x^{\frac{1}{3}}}} = \int_0^1 \frac{3x^{\frac{2}{3}}}{\sqrt{1-x^{\frac{1}{3}}}} d(x^{\frac{1}{3}})"
        )
        assert result is True

    def test_step2_variable_change(self):
        """S2: After substitution t = x^(1/3)."""
        result = sympy_verify_step(
            r"\xlongequal{t=x^{\frac{1}{3}}} \int_0^1 \frac{3t^2}{\sqrt{1-t}} dt = 3\int_0^1 t^2(1-t)^{-\frac{1}{2}} dt"
        )
        assert result is True

    def test_step3_beta_gamma_chain_returns_none(self):
        """S3: Chained Beta/Gamma equality - too complex for SymPy, should return None."""
        result = sympy_verify_step(
            r"= B(3, \frac{1}{2}) = \frac{\Gamma(3)\Gamma(\frac{1}{2})}{\Gamma(\frac{7}{2})} = \frac{2\sqrt{\pi}}{\frac{5}{2}\Gamma(\frac{5}{2})} = \frac{16}{15}"
        )
        assert result is None

    def test_simple_integral_equality(self):
        """Simple integral equality that SymPy can evaluate."""
        result = sympy_verify_step(
            r"\int_0^1 2*x dx = 1"
        )
        assert result is True

    def test_wrong_integral_result(self):
        """Incorrect integral result that SymPy should detect."""
        result = sympy_verify_step(
            r"\int_0^1 2*x dx = 2"
        )
        assert result is False

    def test_polynomial_integral(self):
        """Polynomial integral evaluation."""
        result = sympy_verify_step(
            r"\int_0^1 x^2 dx = \frac{1}{3}"
        )
        assert result is True

    def test_trig_integral(self):
        """Trigonometric integral."""
        result = sympy_verify_step(
            r"\int_0^{\pi} \sin(x) dx = 2"
        )
        assert result is True

    def test_coefficient_times_integral(self):
        """Coefficient multiplied by integral."""
        result = sympy_verify_step(
            r"3\int_0^1 x^2 dx = 1"
        )
        assert result is True
