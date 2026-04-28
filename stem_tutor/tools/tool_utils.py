"""Shared utilities for SymPy-based computation tools."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import Any, Callable, Optional

import sympy
from sympy import Symbol, symbols, sympify

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 5.0

_DEFAULT_SYMBOLS: dict[str, Any] = {
    "x": symbols("x", real=True),
    "y": symbols("y", real=True),
    "z": symbols("z", real=True),
    "t": symbols("t", real=True),
    "u": symbols("u", real=True),
    "v": symbols("v", real=True),
    "n": symbols("n", integer=True),
    "a": symbols("a", real=True),
    "b": symbols("b", real=True),
    "c": symbols("c", real=True),
    "pi": sympy.pi,
    "e": sympy.E,
    "oo": sympy.oo,
}

_DEFAULT_FUNCTIONS: dict[str, Any] = {
    "sin": sympy.sin,
    "cos": sympy.cos,
    "tan": sympy.tan,
    "cot": sympy.cot,
    "sec": sympy.sec,
    "csc": sympy.csc,
    "asin": sympy.asin,
    "acos": sympy.acos,
    "atan": sympy.atan,
    "sinh": sympy.sinh,
    "cosh": sympy.cosh,
    "tanh": sympy.tanh,
    "log": sympy.log,
    "ln": sympy.log,
    "exp": sympy.exp,
    "sqrt": sympy.sqrt,
    "Abs": sympy.Abs,
    "factorial": sympy.factorial,
    "gamma": sympy.gamma,
}

_SYMPY_LOCALS = {**_DEFAULT_SYMBOLS, **_DEFAULT_FUNCTIONS}


def _get_timeout() -> float:
    try:
        from stem_tutor.settings import sympy_timeout
        return sympy_timeout()
    except Exception:
        return DEFAULT_TIMEOUT


def run_with_timeout(func: Callable[[], Any], timeout: float | None = None) -> str:
    timeout = timeout or _get_timeout()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(func)
        try:
            result = future.result(timeout=timeout)
            return str(result)
        except FuturesTimeout:
            return f"[Error: computation timed out after {timeout}s]"
        except Exception as exc:
            return f"[Error: {exc}]"


def safe_sympify(expression: str) -> Optional[sympy.Expr]:
    try:
        expr = sympify(expression, locals=_SYMPY_LOCALS, evaluate=False)
        if expr is None or not isinstance(expr, sympy.Basic):
            return None
        return expr
    except Exception:
        return None


def parse_matrix(matrix_str: str) -> Optional[sympy.Matrix]:
    import json
    s = matrix_str.strip()
    try:
        rows = json.loads(s)
        if isinstance(rows, list) and all(isinstance(r, list) for r in rows):
            return sympy.Matrix(rows)
    except (json.JSONDecodeError, ValueError):
        pass
    try:
        expr = sympify(s, locals=_SYMPY_LOCALS)
        if isinstance(expr, sympy.Matrix):
            return expr
    except Exception:
        pass
    return None


def format_result(result: Any) -> str:
    latex_str = sympy.latex(result)
    return f"{result}  (LaTeX: {latex_str})"
