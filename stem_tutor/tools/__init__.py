"""Computation tools for the STEM Tutor LangGraph agent."""
from stem_tutor.tools.execute_python import execute_python
from stem_tutor.tools.calculus_tools import (
    compute_derivative,
    compute_integral,
    compute_limit,
    compute_pipeline,
    compute_series,
    simplify_expression,
    solve_equation,
    solve_ode,
)
from stem_tutor.tools.matrix_tools import (
    matrix_add,
    matrix_determinant,
    matrix_eigenvalues,
    matrix_eigenvectors,
    matrix_inverse,
    matrix_multiply,
    matrix_rank,
    matrix_rref,
    matrix_trace,
    matrix_transpose,
    solve_linear_system,
)

LEGACY_TOOLS = [
    compute_pipeline,
    compute_derivative,
    compute_integral,
    compute_limit,
    compute_series,
    solve_equation,
    solve_ode,
    simplify_expression,
    matrix_multiply,
    matrix_add,
    matrix_inverse,
    matrix_determinant,
    matrix_eigenvalues,
    matrix_eigenvectors,
    matrix_rank,
    matrix_rref,
    matrix_transpose,
    matrix_trace,
    solve_linear_system,
]


def _build_all_tools():
    from stem_tutor.settings import is_legacy_tools_enabled
    tools = [execute_python, compute_pipeline]
    if is_legacy_tools_enabled():
        tools.extend(LEGACY_TOOLS)
    return tools


ALL_TOOLS = [execute_python, compute_pipeline] + LEGACY_TOOLS


def get_tools():
    return _build_all_tools()
