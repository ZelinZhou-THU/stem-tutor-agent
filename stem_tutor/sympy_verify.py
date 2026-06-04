"""SymPy-based symbolic verification for calculus steps."""
from __future__ import annotations

import logging
import random
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import Optional

import sympy
from sympy import (
    Symbol, symbols, simplify, trigsimp, powsimp, expand, cancel, collect,
    sympify, Rational, S, lambdify, nsimplify
)

logger = logging.getLogger(__name__)

_DEFAULT_SYMBOLS = {
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
    "inf": sympy.oo,
}

_DEFAULT_FUNCTIONS = {
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
    "abs": sympy.Abs,
    "floor": sympy.floor,
    "ceiling": sympy.ceiling,
    "factorial": sympy.factorial,
    "gamma": sympy.gamma,
    "beta": sympy.beta,
    "integrate": sympy.integrate,
    "diff": sympy.diff,
    "limit": sympy.limit,
    "Sum": sympy.Sum,
    "Product": sympy.Product,
}

SYMPY_TIMEOUT = 3.0
MAX_AST_SIZE = 200
NUMERIC_SAMPLES = 15
NUMERIC_TOLERANCE = 1e-8


def _get_sympy_timeout() -> float:
    try:
        from stem_tutor.settings import sympy_timeout
        return sympy_timeout()
    except Exception:
        return SYMPY_TIMEOUT


def _find_brace_end(text: str, start: int) -> int:
    if start >= len(text) or text[start] != '{':
        return -1
    depth = 0
    i = start
    while i < len(text):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _latex_to_sympy(text: str) -> str:
    result = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] == '\\' and i + 1 < n and text[i + 1].isalpha():
            j = i + 1
            while j < n and text[j].isalpha():
                j += 1
            cmd = text[i + 1:j]

            if cmd == 'left' or cmd == 'right':
                if j < n and text[j] in '()[]|{}':
                    i = j + 1
                else:
                    i = j
                continue

            if cmd == 'xlongequal':
                if j < n and text[j] == '{':
                    end = _find_brace_end(text, j)
                    if end >= 0:
                        i = end + 1
                        result.append('=')
                        continue
                result.append('=')
                i = j
                continue

            if cmd == 'frac':
                if j < n and text[j] == '{':
                    num_end = _find_brace_end(text, j)
                    if num_end >= 0:
                        num = text[j + 1:num_end]
                        rest = text[num_end + 1:]
                        if rest.startswith('{'):
                            den_end = _find_brace_end(rest, 0)
                            if den_end >= 0:
                                den = rest[1:den_end]
                                result.append(f"({_latex_to_sympy(num)})/({_latex_to_sympy(den)})")
                                i = num_end + 1 + 1 + den_end + 1
                                continue
                result.append(text[i])
                i += 1
                continue

            if cmd == 'sqrt':
                if j < n and text[j] == '[':
                    k = text.find(']', j + 1)
                    if k >= 0 and k + 1 < n and text[k + 1] == '{':
                        root_idx = text[j + 1:k]
                        body_end = _find_brace_end(text, k + 1)
                        if body_end >= 0:
                            body = text[k + 2:body_end]
                            result.append(f"({_latex_to_sympy(body)})**(1/({_latex_to_sympy(root_idx)}))")
                            i = body_end + 1
                            continue
                if j < n and text[j] == '{':
                    end = _find_brace_end(text, j)
                    if end >= 0:
                        body = text[j + 1:end]
                        result.append(f"sqrt({_latex_to_sympy(body)})")
                        i = end + 1
                        continue
                result.append('sqrt')
                i = j
                continue

            if cmd in ('int', 'integrate'):
                lower = ''
                upper = ''
                k = j
                while k < n and text[k] in ('_', '^'):
                    marker = text[k]
                    if k + 1 < n and text[k + 1] == '{':
                        end = _find_brace_end(text, k + 1)
                        if end >= 0:
                            val = text[k + 2:end]
                            if marker == '_':
                                lower = val
                            else:
                                upper = val
                            k = end + 1
                        else:
                            break
                    elif k + 1 < n and (text[k + 1].isalnum() or text[k + 1] == '-'):
                        m = k + 1
                        while m < n and (text[m].isalnum() or text[m] in '-+'):
                            m += 1
                            val = text[k + 1:m]
                            if marker == '_':
                                lower = val
                            else:
                                upper = val
                            k = m
                    else:
                        break
                result.append(f"__INT_{lower}_{upper}__")
                i = k
                continue

            if cmd in ('Gamma', 'gamma'):
                if j < n and text[j] == '{':
                    end = _find_brace_end(text, j)
                    if end >= 0:
                        body = text[j + 1:end]
                        result.append(f"gamma({_latex_to_sympy(body)})")
                        i = end + 1
                        continue
                result.append('gamma')
                i = j
                continue

            if cmd in ('times', 'cdot'):
                result.append('*')
                i = j
                continue

            if cmd == 'pi':
                result.append('pi')
                i = j
                continue

            if cmd == 'infty':
                result.append('oo')
                i = j
                continue

            if cmd == 'sum':
                result.append('Sum')
                i = j
                continue

            if cmd == 'prod':
                result.append('Product')
                i = j
                continue

            if j < n and text[j] == '{':
                end = _find_brace_end(text, j)
                if end >= 0:
                    body = text[j + 1:end]
                    result.append(f"{cmd}({_latex_to_sympy(body)})")
                    i = end + 1
                    continue
            result.append(cmd)
            i = j
        elif text[i] == '^':
            if i + 1 < n and text[i + 1] == '{':
                end = _find_brace_end(text, i + 1)
                if end >= 0:
                    body = text[i + 2:end]
                    result.append(f"**({_latex_to_sympy(body)})")
                    i = end + 1
                    continue
            elif i + 1 < n and (text[i + 1].isalnum() or text[i + 1] == '-'):
                m = i + 1
                while m < n and (text[m].isalnum() or text[m] in '-+'):
                    m += 1
                result.append(f"**{text[i+1:m]}")
                i = m
                continue
            else:
                result.append('**')
                i += 1
        elif text[i] == 'π':
            result.append('pi')
            i += 1
        elif text[i] == '∞':
            result.append('oo')
            i += 1
        else:
            result.append(text[i])
            i += 1
    return ''.join(result)


def _get_sympy_rules() -> tuple[list[str], list[tuple[str, str]]]:
    try:
        from stem_tutor.subjects.context import get_subject_context
        from stem_tutor.prompts.templates import _current_subject_id
        ctx = get_subject_context(_current_subject_id())
        return ctx.sympy_strip_prefixes, ctx.sympy_derivative_patterns
    except Exception:
        return (
            ["解：", "解:", "therefore", "Thus", "So", "令", "设", "即", "得", "故", "所以", "因为", "where", "given"],
            [
                (r"d/dx\[(.+?)\]", r"diff(\1, x)"),
                (r"d/dt\[(.+?)\]", r"diff(\1, t)"),
                (r"d/du\[(.+?)\]", r"diff(\1, u)"),
                (r"d\(([^)]+)\)", r"*\1"),
            ],
        )


def _postprocess(s: str) -> str:
    strip_prefixes, derivative_patterns = _get_sympy_rules()
    if strip_prefixes:
        prefix_pattern = "|".join(re.escape(p) for p in strip_prefixes)
        s = re.sub(r"^(" + prefix_pattern + r")\s*", "", s, flags=re.IGNORECASE)
    for pattern, replacement in derivative_patterns:
        s = re.sub(pattern, replacement, s)
    s = s.replace("^", "**")
    s = re.sub(r"(\d)([a-zA-Z(])", r"\1*\2", s)
    s = re.sub(r"(\))(\d|[a-zA-Z(])", r"\1*\2", s)
    s = re.sub(r"([a-zA-Z])(\d)", r"\1*\2", s)
    s = re.sub(r"\s+", " ", s)
    s = s.replace("\\", "")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _split_chained_equalities(text: str) -> list[str]:
    parts = []
    depth = 0
    current = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in '([{':
            depth += 1
            current.append(ch)
        elif ch in ')]}':
            depth -= 1
            current.append(ch)
        elif ch == '=' and depth == 0:
            parts.append(''.join(current).strip())
            current = []
        else:
            current.append(ch)
        i += 1
    if current:
        parts.append(''.join(current).strip())
    return [p for p in parts if p]


def _extract_integral(text: str) -> Optional[sympy.Expr]:
    m = re.match(r"^(\d*)\s*__INT_([^_]*)_([^_]*)__\s*(.+)", text)
    if not m:
        return None
    coeff = m.group(1)
    lower = m.group(2).strip()
    upper = m.group(3).strip()
    integrand_raw = m.group(4).strip()
    integrand_raw = re.sub(r"\s*\*?\s*d\s*\(\s*([a-zA-Z])\s*\)\s*$", "", integrand_raw)
    integrand_raw = re.sub(r"\s*\*?\s*d\s*([a-zA-Z])\s*$", "", integrand_raw)
    integrand_raw = integrand_raw.strip()
    if not integrand_raw:
        return None
    try:
        var_match = re.search(r"d\s*\(\s*([a-zA-Z])\s*\)", text)
        if var_match:
            var = var_match.group(1)
        else:
            var_match2 = re.search(r"\s+d\s*([a-zA-Z])\s*$", text)
            if var_match2:
                var = var_match2.group(1)
            else:
                var_match3 = re.search(r"\*d\s*([a-zA-Z])\s*$", text)
                var = var_match3.group(1) if var_match3 else 'x'
        integrand = sympify(integrand_raw, locals={**_DEFAULT_SYMBOLS, **_DEFAULT_FUNCTIONS}, evaluate=False)
        integration_var = _DEFAULT_SYMBOLS.get(var, sympy.Symbol(var))
        if lower and upper:
            lower_expr = sympify(lower, locals=_DEFAULT_SYMBOLS, evaluate=False)
            upper_expr = sympify(upper, locals=_DEFAULT_SYMBOLS, evaluate=False)
            result = sympy.integrate(integrand, (integration_var, lower_expr, upper_expr))
        elif var:
            result = sympy.integrate(integrand, integration_var)
        else:
            return None
        if result.has(sympy.Integral) or result == integrand:
            return None
        if coeff:
            coeff_expr = sympify(coeff, locals=_DEFAULT_SYMBOLS, evaluate=False)
            result = coeff_expr * result
        return result
    except Exception as e:
        logger.debug("[sympy] integral parse failed: %s", e)
    return None


def _preprocess(text: str) -> str:
    s = text.strip()
    s = _latex_to_sympy(s)
    s = _postprocess(s)
    return s


def extract_expressions(text: str) -> list[str]:
    cleaned = _preprocess(text)
    if not cleaned:
        return []
    parts = _split_chained_equalities(cleaned)
    if len(parts) >= 2:
        return parts
    if "=" in cleaned:
        idx = cleaned.index("=")
        lhs = cleaned[:idx].strip()
        rhs = cleaned[idx + 1:].strip()
        result = []
        if lhs:
            result.append(lhs)
        if rhs:
            result.append(rhs)
        return result if result else [cleaned]
    return [cleaned] if cleaned else []


def safe_parse_expr(text: str) -> Optional[sympy.Expr]:
    if not text or not text.strip():
        return None
    processed = _preprocess(text)
    if not processed:
        return None
    integral_result = _extract_integral(processed)
    if integral_result is not None:
        return integral_result
    has_math = bool(re.search(r"[\+\-\*/\^=()]", processed) or re.search(r"\d", processed) or re.search(r"(sin|cos|tan|cot|sec|csc|sqrt|log|exp|diff|integrate|limit|Sum|Product|asin|acos|atan|sinh|cosh|tanh|Abs|floor|ceiling|factorial|gamma|beta)", processed, re.IGNORECASE))
    if not has_math:
        return None
    try:
        expr = sympify(processed, locals={**_DEFAULT_SYMBOLS, **_DEFAULT_FUNCTIONS}, evaluate=False)
        if expr is None or not isinstance(expr, sympy.Basic):
            return None
        if expr.is_Symbol and str(expr) in _DEFAULT_SYMBOLS:
            return None
        return expr
    except Exception as e:
        logger.debug("[sympy] parse failed for %r: %s", text[:80], e)
        return None


_RATIONAL_SAMPLE_POINTS = [
    Rational(-5, 1), Rational(-3, 1), Rational(-2, 1), Rational(-3, 2),
    Rational(-1, 1), Rational(-1, 2), Rational(-1, 3), Rational(1, 3),
    Rational(1, 2), Rational(1, 1), Rational(3, 2), Rational(2, 1),
    Rational(3, 1), Rational(5, 1), Rational(7, 2),
]


def _numeric_check(expr1: sympy.Expr, expr2: sympy.Expr, n_points: int = NUMERIC_SAMPLES) -> Optional[bool]:
    symbols_list = list((expr1.free_symbols | expr2.free_symbols))
    if not symbols_list:
        try:
            v1 = complex(expr1.evalf())
            v2 = complex(expr2.evalf())
            return abs(v1 - v2) < NUMERIC_TOLERANCE
        except Exception:
            return None

    try:
        f1 = lambdify(symbols_list, expr1, modules='numpy')
        f2 = lambdify(symbols_list, expr2, modules='numpy')
    except Exception:
        f1, f2 = None, None

    for _ in range(n_points):
        subs = {}
        for sym in symbols_list:
            val = random.choice(_RATIONAL_SAMPLE_POINTS)
            subs[sym] = val
        try:
            if f1 is not None and f2 is not None:
                float_subs = {str(s): float(v) for s, v in subs.items()}
                v1 = complex(f1(**float_subs))
                v2 = complex(f2(**float_subs))
            else:
                v1 = complex(expr1.subs(subs).evalf())
                v2 = complex(expr2.subs(subs).evalf())
            if abs(v1 - v2) > NUMERIC_TOLERANCE * max(1, abs(v1), abs(v2)):
                return False
        except Exception:
            continue
    return True


def _check_equivalence_safe(expr1: sympy.Expr, expr2: sympy.Expr) -> Optional[bool]:
    if expr1 == expr2:
        return True
    try:
        diff_expr = simplify(expr1 - expr2)
        if diff_expr == 0:
            return True
        if diff_expr.is_number and diff_expr != 0:
            return False
    except Exception:
        pass
    try:
        if trigsimp(expr1 - expr2) == 0:
            return True
    except Exception:
        pass
    try:
        if powsimp(expr1 - expr2) == 0:
            return True
    except Exception:
        pass
    try:
        if cancel(expr1 - expr2) == 0:
            return True
    except Exception:
        pass
    try:
        if expr2 != 0 and simplify(expr1 / expr2) == 1:
            return True
    except Exception:
        pass
    try:
        if expand(expr1) == expand(expr2):
            return True
    except Exception:
        pass
    return _numeric_check(expr1, expr2)


def _check_with_timeout(expr1: sympy.Expr, expr2: sympy.Expr, timeout: float = SYMPY_TIMEOUT) -> Optional[bool]:
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_check_equivalence_safe, expr1, expr2)
        try:
            return future.result(timeout=timeout)
        except FuturesTimeout:
            logger.warning("[sympy] equivalence check timed out")
            return None
        except Exception as e:
            logger.debug("[sympy] equivalence check error: %s", e)
            return None


def sympy_verify_step(step_text: str, prev_text: str = "", reference_text: str = "") -> Optional[bool]:
    if not step_text or not step_text.strip():
        return None
    step_exprs = extract_expressions(step_text)
    if not step_exprs:
        return None
    if len(step_exprs) == 2:
        lhs_str, rhs_str = step_exprs
        lhs = safe_parse_expr(lhs_str)
        rhs = safe_parse_expr(rhs_str)
        if lhs is None or rhs is None:
            return None
        return _check_with_timeout(lhs, rhs)
    if len(step_exprs) == 1:
        step_expr = safe_parse_expr(step_exprs[0])
        if step_expr is None:
            return None
        context_exprs = []
        if prev_text:
            prev_exprs = extract_expressions(prev_text)
            for pe in prev_exprs:
                expr = safe_parse_expr(pe)
                if expr is not None:
                    context_exprs.append(expr)
        if reference_text:
            ref_exprs = extract_expressions(reference_text)
            for re_expr in ref_exprs[:3]:
                expr = safe_parse_expr(re_expr)
                if expr is not None:
                    context_exprs.append(expr)
        if not context_exprs:
            return None
        for ctx_expr in context_exprs:
            result = _check_with_timeout(step_expr, ctx_expr)
            if result is True:
                return True
        return None
    return None
