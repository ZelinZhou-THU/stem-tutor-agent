"""Calculus computation tools for LangGraph agent."""
from __future__ import annotations

import re
from typing import Optional

import sympy
from langchain_core.tools import tool

from stem_tutor.tools.tool_utils import (
    format_result,
    run_with_timeout,
    safe_sympify,
    _DEFAULT_SYMBOLS,
    _SYMPY_LOCALS,
)


@tool
def compute_derivative(expression: str, variable: str) -> str:
    """计算函数关于指定变量的导数。

    Args:
        expression: 数学表达式字符串，如 'x**3 + 2*x' 或 'sin(x)'
        variable: 求导变量，如 'x' 或 't'

    Returns:
        导数结果，包含纯文本和 LaTeX 格式
    """
    def _compute():
        expr = safe_sympify(expression)
        if expr is None:
            return f"[Error: cannot parse expression '{expression}']"
        var = _DEFAULT_SYMBOLS.get(variable, sympy.Symbol(variable))
        result = sympy.diff(expr, var)
        return format_result(result)
    return run_with_timeout(_compute)


@tool
def compute_integral(expression: str, variable: str, lower: str = "", upper: str = "") -> str:
    """计算函数的积分。如果不提供上下限则计算不定积分。

    Args:
        expression: 被积表达式，如 'x**2'
        variable: 积分变量，如 'x'
        lower: 积分下限（可选），如 '0'
        upper: 积分上限（可选），如 '1'

    Returns:
        积分结果，包含纯文本和 LaTeX 格式
    """
    def _compute():
        expr = safe_sympify(expression)
        if expr is None:
            return f"[Error: cannot parse expression '{expression}']"
        var = _DEFAULT_SYMBOLS.get(variable, sympy.Symbol(variable))
        if lower and upper:
            a = safe_sympify(lower)
            b = safe_sympify(upper)
            if a is None or b is None:
                return f"[Error: cannot parse limits '{lower}', '{upper}']"
            result = sympy.integrate(expr, (var, a, b))
        else:
            result = sympy.integrate(expr, var)
        if result.has(sympy.Integral):
            return "[Error: integral cannot be computed symbolically]"
        return format_result(result)
    return run_with_timeout(_compute, timeout=10.0)


@tool
def compute_limit(expression: str, variable: str, point: str, direction: str = "+") -> str:
    """计算函数在某点的极限。

    Args:
        expression: 数学表达式，如 'sin(x)/x'
        variable: 变量名，如 'x'
        point: 极限点，如 '0' 或 'oo'（无穷）
        direction: 趋近方向，'+'（右极限）或 '-'（左极限）

    Returns:
        极限结果，包含纯文本和 LaTeX 格式
    """
    def _compute():
        expr = safe_sympify(expression)
        if expr is None:
            return f"[Error: cannot parse expression '{expression}']"
        var = _DEFAULT_SYMBOLS.get(variable, sympy.Symbol(variable))
        pt = safe_sympify(point)
        if pt is None:
            return f"[Error: cannot parse point '{point}']"
        result = sympy.limit(expr, var, pt, direction)
        return format_result(result)
    return run_with_timeout(_compute)


@tool
def compute_series(expression: str, variable: str, point: str = "0", order: int = 6) -> str:
    """计算函数的泰勒级数展开。

    Args:
        expression: 数学表达式，如 'exp(x)'
        variable: 变量名，如 'x'
        point: 展开点，默认 '0'
        order: 展开阶数，默认 6

    Returns:
        泰勒展开结果，包含纯文本和 LaTeX 格式
    """
    def _compute():
        expr = safe_sympify(expression)
        if expr is None:
            return f"[Error: cannot parse expression '{expression}']"
        var = _DEFAULT_SYMBOLS.get(variable, sympy.Symbol(variable))
        pt = safe_sympify(point)
        if pt is None:
            return f"[Error: cannot parse point '{point}']"
        result = sympy.series(expr, var, pt, n=order)
        return format_result(result)
    return run_with_timeout(_compute)


@tool
def solve_equation(equation: str, variable: str) -> str:
    """求解方程。equation 格式为 'lhs = rhs'。

    Args:
        equation: 方程字符串，如 'x**2 - 4 = 0'
        variable: 未知变量，如 'x'

    Returns:
        方程的解
    """
    def _compute():
        if "=" not in equation:
            return f"[Error: equation must contain '=', got '{equation}']"
        lhs_str, rhs_str = equation.split("=", 1)
        lhs = safe_sympify(lhs_str.strip())
        rhs = safe_sympify(rhs_str.strip())
        if lhs is None or rhs is None:
            return f"[Error: cannot parse equation '{equation}']"
        var = _DEFAULT_SYMBOLS.get(variable, sympy.Symbol(variable))
        result = sympy.solve(lhs - rhs, var)
        return format_result(result)
    return run_with_timeout(_compute, timeout=10.0)


@tool
def solve_ode(equation: str, func_var: str = "y", indep_var: str = "x") -> str:
    """求解常微分方程。equation 中用 func_var(indep_var) 表示未知函数，用 diff(...) 表示导数。

    Args:
        equation: 微分方程，如 "diff(y(x), x) - y(x) = 0"
        func_var: 函数变量名，默认 'y'
        indep_var: 自变量名，默认 'x'

    Returns:
        微分方程的通解
    """
    def _compute():
        from sympy import Function, Eq, dsolve, Derivative
        f = Function(func_var)
        x_sym = _DEFAULT_SYMBOLS.get(indep_var, sympy.Symbol(indep_var))
        local_dict = {
            **_SYMPY_LOCALS,
            func_var: f,
            "diff": lambda expr, *_args: Derivative(expr, *_args),
        }
        parsed = safe_sympify(equation)
        if parsed is None:
            parsed = sympify(equation, locals=local_dict, evaluate=False)
        if "=" in equation:
            lhs_str, rhs_str = equation.split("=", 1)
            lhs = sympify(lhs_str.strip(), locals=local_dict)
            rhs = sympify(rhs_str.strip(), locals=local_dict)
            ode = Eq(lhs, rhs)
        else:
            ode = Eq(parsed, 0)
        result = dsolve(ode, f(x_sym))
        return format_result(result)
    return run_with_timeout(_compute, timeout=10.0)


@tool
def simplify_expression(expression: str) -> str:
    """化简数学表达式。

    Args:
        expression: 待化简的表达式，如 '(x**2 - 1)/(x - 1)'

    Returns:
        化简后的表达式
    """
    def _compute():
        expr = safe_sympify(expression)
        if expr is None:
            return f"[Error: cannot parse expression '{expression}']"
        result = sympy.simplify(expr)
        return format_result(result)
    return run_with_timeout(_compute, timeout=10.0)


_PIPELINE_REFS_RE = re.compile(r"\$(\d+)")


def _resolve_pipeline_refs(expr_str: str, results: list[str]) -> str:
    def _replacer(m):
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(results):
            raw = results[idx]
            if raw.startswith("[Error"):
                return expr_str
            for candidate in raw.split("(LaTeX:"):
                candidate = candidate.strip()
                if candidate and not candidate.startswith("LaTeX"):
                    return candidate.rstrip(")")
            return raw
        return expr_str
    return _PIPELINE_REFS_RE.sub(_replacer, expr_str)


def _parse_pipeline_step(step: str, results: list[str]):
    s = step.strip()
    s = _resolve_pipeline_refs(s, results)

    if s.startswith("diff(") and s.endswith(")"):
        inner = s[5:-1]
        parts = [p.strip() for p in inner.split(",")]
        if len(parts) == 2:
            return "diff", parts[0], parts[1]
        if len(parts) == 3:
            return "diff", parts[0], parts[1]

    if s.startswith("integrate(") and s.endswith(")"):
        inner = s[10:-1]
        parts = [p.strip() for p in inner.split(",")]
        if len(parts) == 4:
            return "integrate", parts[0], parts[1], parts[2], parts[3]
        if len(parts) == 3:
            return "integrate_def", parts[0], parts[1], parts[2]
        if len(parts) == 2:
            return "integrate", parts[0], parts[1]

    if s.startswith("limit(") and s.endswith(")"):
        inner = s[6:-1]
        parts = [p.strip() for p in inner.split(",")]
        if len(parts) == 3:
            return "limit", parts[0], parts[1], parts[2]

    if s.startswith("simplify(") and s.endswith(")"):
        inner = s[9:-1]
        return "simplify", inner

    if s.startswith("solve(") and s.endswith(")"):
        inner = s[6:-1]
        parts = [p.strip() for p in inner.split(",")]
        if len(parts) == 2:
            return "solve", parts[0], parts[1]

    if s.startswith("series(") and s.endswith(")"):
        inner = s[7:-1]
        parts = [p.strip() for p in inner.split(",")]
        if len(parts) == 3:
            return "series", parts[0], parts[1], int(parts[2])
        if len(parts) == 2:
            return "series", parts[0], parts[1], 6

    return "raw_expr", s


def _execute_pipeline_step(parsed, results):
    op = parsed[0]
    if op == "diff":
        _, expr_str, var_str = parsed
        expr = safe_sympify(expr_str)
        if expr is None:
            return f"[Error: cannot parse '{expr_str}']"
        var = _DEFAULT_SYMBOLS.get(var_str, sympy.Symbol(var_str))
        return format_result(sympy.diff(expr, var))

    if op == "integrate":
        _, expr_str, var_str = parsed
        expr = safe_sympify(expr_str)
        if expr is None:
            return f"[Error: cannot parse '{expr_str}']"
        var = _DEFAULT_SYMBOLS.get(var_str, sympy.Symbol(var_str))
        result = sympy.integrate(expr, var)
        if result.has(sympy.Integral):
            return "[Error: integral cannot be computed symbolically]"
        return format_result(result)

    if op == "integrate_def":
        _, expr_str, var_str, limits_str = parsed
        expr = safe_sympify(expr_str)
        if expr is None:
            return f"[Error: cannot parse '{expr_str}']"
        var = _DEFAULT_SYMBOLS.get(var_str, sympy.Symbol(var_str))
        parts = [p.strip() for p in limits_str.split("to")]
        if len(parts) != 2:
            parts = [p.strip() for p in limits_str.split(",")]
        if len(parts) != 2:
            return f"[Error: invalid limits '{limits_str}']"
        a = safe_sympify(parts[0])
        b = safe_sympify(parts[1])
        if a is None or b is None:
            return f"[Error: cannot parse limits '{limits_str}']"
        result = sympy.integrate(expr, (var, a, b))
        if result.has(sympy.Integral):
            return "[Error: integral cannot be computed symbolically]"
        return format_result(result)

    if op == "limit":
        _, expr_str, var_str, point_str = parsed
        expr = safe_sympify(expr_str)
        if expr is None:
            return f"[Error: cannot parse '{expr_str}']"
        var = _DEFAULT_SYMBOLS.get(var_str, sympy.Symbol(var_str))
        pt = safe_sympify(point_str)
        if pt is None:
            return f"[Error: cannot parse point '{point_str}']"
        return format_result(sympy.limit(expr, var, pt))

    if op == "simplify":
        _, expr_str = parsed
        expr = safe_sympify(expr_str)
        if expr is None:
            return f"[Error: cannot parse '{expr_str}']"
        return format_result(sympy.simplify(expr))

    if op == "solve":
        _, eq_str, var_str = parsed
        if "=" in eq_str:
            lhs_s, rhs_s = eq_str.split("=", 1)
            lhs = safe_sympify(lhs_s.strip())
            rhs = safe_sympify(rhs_s.strip())
            if lhs is None or rhs is None:
                return f"[Error: cannot parse equation '{eq_str}']"
        else:
            lhs = safe_sympify(eq_str)
            rhs = sympy.S.Zero
            if lhs is None:
                return f"[Error: cannot parse '{eq_str}']"
        var = _DEFAULT_SYMBOLS.get(var_str, sympy.Symbol(var_str))
        return format_result(sympy.solve(lhs - rhs, var))

    if op == "series":
        _, expr_str, var_str, order = parsed
        expr = safe_sympify(expr_str)
        if expr is None:
            return f"[Error: cannot parse '{expr_str}']"
        var = _DEFAULT_SYMBOLS.get(var_str, sympy.Symbol(var_str))
        return format_result(sympy.series(expr, var, 0, n=order))

    if op == "raw_expr":
        _, expr_str = parsed
        expr = safe_sympify(expr_str)
        if expr is None:
            return f"[Error: cannot parse '{expr_str}']"
        return format_result(sympy.simplify(expr))

    return f"[Error: unknown operation '{op}']"


@tool
def compute_pipeline(steps: list[str]) -> str:
    """批量执行多步计算，一次工具调用完成多个计算步骤。

    每个步骤使用简洁语法：
    - "diff(表达式, 变量)" — 求导
    - "integrate(表达式, 变量)" — 不定积分
    - "integrate(表达式, 变量, 下限, 上限)" — 定积分（上下限用逗号或 to 分隔）
    - "limit(表达式, 变量, 极限点)" — 极限
    - "simplify(表达式)" — 化简
    - "solve(方程, 变量)" — 解方程（方程含等号，如 "x^2-4=0"）
    - "series(表达式, 变量, 阶数)" — 泰勒展开（阶数可选，默认6）

    可用 $1, $2, ... 引用前面步骤的结果（$1 表示第1步结果）。

    示例：
    ["diff(x^3 + 2*x, x)", "integrate($1, x, 0, 1)", "simplify($2)"]

    Args:
        steps: 计算步骤列表

    Returns:
        每步计算的结果，带编号
    """
    def _compute():
        results: list[str] = []
        lines: list[str] = []
        for i, step in enumerate(steps, 1):
            parsed = _parse_pipeline_step(step, results)
            result = _execute_pipeline_step(parsed, results)
            results.append(result)
            lines.append(f"步骤{i}: {step} → {result}")
        return "\n".join(lines)

    return run_with_timeout(_compute, timeout=15.0)
