"""General-purpose Python code execution tool for mathematical computation."""
from __future__ import annotations

import logging
import os
import subprocess
import sys

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 10
_MAX_OUTPUT = 4000
_BUDGET_TIMEOUT_ENV = "STEM_TUTOR_CURRENT_TOOL_TIMEOUT"

_BLACKLISTED_PATTERNS = [
    (".integrate(", "sp.integrate / sympy.integrate is prohibited for definite integrals. Use scipy.integrate.quad for numerical integration, or sp.beta/sp.gamma for special functions"),
]


def _get_timeout() -> int:
    env_val = os.environ.get(_BUDGET_TIMEOUT_ENV, "").strip()
    if env_val:
        try:
            return max(1, int(env_val))
        except ValueError:
            pass
    try:
        from stem_tutor.settings import python_sandbox_timeout
        t = python_sandbox_timeout()
        return max(1, int(t))
    except Exception:
        return _DEFAULT_TIMEOUT


def _check_blacklist(code: str) -> str | None:
    for pattern, suggestion in _BLACKLISTED_PATTERNS:
        if pattern in code:
            safe_callers = ("scipy.integrate", "quad(", "scipy.", "integrate.quad")
            line_start = 0
            for line in code.split("\n"):
                stripped = line.strip()
                if pattern in stripped and not stripped.startswith("#"):
                    is_safe = any(s in stripped for s in safe_callers)
                    if not is_safe:
                        return (
                            f"[Error Type: Blacklisted | Detail: '{pattern}' is not allowed in "
                            f"computation code | Suggestion: {suggestion}]"
                        )
    return None


@tool
def execute_python(code: str) -> str:
    """执行 Python 代码进行数学/科学计算。

    可用库: sympy (符号计算), numpy (数值计算), scipy (科学计算), math, fractions, json 等。
    用 print() 输出结果，所有计算写在一个代码块中一次性提交。
    代码在独立子进程中运行，有超时限制。

    禁止操作:
    - sympy.integrate / sp.integrate (请用 scipy.integrate.quad 或 sp.beta/sp.gamma)

    示例:
        from sympy import *
        x = symbols('x')
        f = x**3 * exp(2*x)
        f_prime = diff(f, x)
        print(f"f'(x) = {f_prime}")
        print(f"f'(1) = {f_prime.subs(x, 1).simplify()}")

    Args:
        code: 要执行的 Python 代码

    Returns:
        代码的 stdout 输出，或错误信息
    """
    blacklist_error = _check_blacklist(code)
    if blacklist_error:
        return blacklist_error

    timeout = _get_timeout()

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"

    proc = subprocess.Popen(
        [sys.executable, "-u", "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    try:
        stdout_bytes, stderr_bytes = proc.communicate(timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout_bytes, stderr_bytes = proc.communicate()
        timed_out = True

    stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
    stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

    if timed_out:
        if stdout.strip():
            partial = stdout.strip()
            if len(partial) > _MAX_OUTPUT:
                partial = partial[:_MAX_OUTPUT] + f"\n... [truncated, {len(partial)} chars total]"
            return (
                f"[Warning: partial output (timed out after {timeout}s)]\n{partial}"
            )
        suggestion = (
            "Suggestion: switch to numerical methods (scipy.integrate.quad, numpy) "
            "instead of symbolic computation (sympy.integrate), or break the problem "
            "into smaller independent steps"
        )
        return (
            f"[Error Type: Timeout | Detail: code execution timed out after {timeout}s | "
            f"{suggestion}]"
        )

    if proc.returncode != 0:
        error_lines = stderr.strip().splitlines()
        full_stderr = "\n".join(error_lines)

        if "UnicodeEncodeError" in full_stderr or "gbk codec can't encode" in full_stderr:
            return f"[Error Type: UnicodeEncodeError | Detail: {full_stderr[-200:]} | Suggestion: use ASCII-only output in print(), avoid Unicode characters like ∫ √ ₀ ¹]"

        if "SyntaxError" in full_stderr:
            for line in error_lines:
                if "SyntaxError" in line:
                    return f"[Error Type: SyntaxError | Detail: {line.strip()} | Suggestion: check Python syntax, parentheses, and indentation]"

        if "ImportError" in full_stderr or "ModuleNotFoundError" in full_stderr:
            for line in error_lines:
                if "ImportError" in line or "ModuleNotFoundError" in line:
                    return f"[Error Type: ImportError | Detail: {line.strip()} | Suggestion: only use allowed libraries (sympy, numpy, scipy, math, fractions, json)]"

        if "Timeout" in full_stderr or "timed out" in full_stderr:
            return f"[Error Type: Timeout | Detail: {full_stderr[-200:]} | Suggestion: reduce computational complexity or switch to numerical approximation]"

        clean = "\n".join(error_lines[-5:])
        return f"[Error Type: RuntimeError | ExitCode: {proc.returncode} | Detail: {clean} | Suggestion: review code logic and error trace above]"

    if not stdout.strip() and stderr.strip():
        return f"[Warning: no output. stderr: {stderr.strip()[-500:]}]"

    output = stdout.strip()
    if len(output) > _MAX_OUTPUT:
        output = output[:_MAX_OUTPUT] + f"\n... [truncated, {len(output)} chars total]"
    return output
