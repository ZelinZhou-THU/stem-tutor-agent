"""Linear algebra / matrix computation tools for LangGraph agent."""
from __future__ import annotations

import sympy
from langchain_core.tools import tool

from stem_tutor.tools.tool_utils import (
    format_result,
    parse_matrix,
    run_with_timeout,
    safe_sympify,
    _DEFAULT_SYMBOLS,
)


@tool
def matrix_multiply(matrix_a: str, matrix_b: str) -> str:
    """计算两个矩阵的乘积。

    Args:
        matrix_a: 第一个矩阵，JSON 格式如 '[[1,2],[3,4]]'
        matrix_b: 第二个矩阵，JSON 格式如 '[[5,6],[7,8]]'

    Returns:
        矩阵乘积结果
    """
    def _compute():
        A = parse_matrix(matrix_a)
        B = parse_matrix(matrix_b)
        if A is None:
            return f"[Error: cannot parse matrix_a '{matrix_a}']"
        if B is None:
            return f"[Error: cannot parse matrix_b '{matrix_b}']"
        if A.cols != B.rows:
            return f"[Error: dimension mismatch: {A.shape} x {B.shape}]"
        result = A * B
        return format_result(result)
    return run_with_timeout(_compute)


@tool
def matrix_add(matrix_a: str, matrix_b: str) -> str:
    """计算两个矩阵的和。

    Args:
        matrix_a: 第一个矩阵
        matrix_b: 第二个矩阵

    Returns:
        矩阵和
    """
    def _compute():
        A = parse_matrix(matrix_a)
        B = parse_matrix(matrix_b)
        if A is None:
            return f"[Error: cannot parse matrix_a '{matrix_a}']"
        if B is None:
            return f"[Error: cannot parse matrix_b '{matrix_b}']"
        result = A + B
        return format_result(result)
    return run_with_timeout(_compute)


@tool
def matrix_inverse(matrix: str) -> str:
    """计算矩阵的逆矩阵。

    Args:
        matrix: 方阵，JSON 格式如 '[[1,2],[3,4]]'

    Returns:
        逆矩阵
    """
    def _compute():
        M = parse_matrix(matrix)
        if M is None:
            return f"[Error: cannot parse matrix '{matrix}']"
        if M.rows != M.cols:
            return f"[Error: matrix is not square: {M.shape}]"
        det = M.det()
        if det == 0:
            return "[Error: matrix is singular, inverse does not exist]"
        result = M.inv()
        return format_result(result)
    return run_with_timeout(_compute, timeout=10.0)


@tool
def matrix_determinant(matrix: str) -> str:
    """计算方阵的行列式。

    Args:
        matrix: 方阵，JSON 格式如 '[[1,2],[3,4]]'

    Returns:
        行列式的值
    """
    def _compute():
        M = parse_matrix(matrix)
        if M is None:
            return f"[Error: cannot parse matrix '{matrix}']"
        if M.rows != M.cols:
            return f"[Error: matrix is not square: {M.shape}]"
        result = M.det()
        return format_result(result)
    return run_with_timeout(_compute, timeout=10.0)


@tool
def matrix_eigenvalues(matrix: str) -> str:
    """计算方阵的特征值。

    Args:
        matrix: 方阵，JSON 格式如 '[[2,1],[1,2]]'

    Returns:
        特征值及其重数
    """
    def _compute():
        M = parse_matrix(matrix)
        if M is None:
            return f"[Error: cannot parse matrix '{matrix}']"
        if M.rows != M.cols:
            return f"[Error: matrix is not square: {M.shape}]"
        eigenvals = M.eigenvals()
        result_str = ", ".join(
            f"{format_result(val)} (重数 {mult})"
            for val, mult in eigenvals.items()
        )
        return result_str
    return run_with_timeout(_compute, timeout=15.0)


@tool
def matrix_eigenvectors(matrix: str) -> str:
    """计算方阵的特征向量。

    Args:
        matrix: 方阵，JSON 格式如 '[[2,1],[1,2]]'

    Returns:
        每个特征值对应的特征向量
    """
    def _compute():
        M = parse_matrix(matrix)
        if M is None:
            return f"[Error: cannot parse matrix '{matrix}']"
        if M.rows != M.cols:
            return f"[Error: matrix is not square: {M.shape}]"
        eigenvects = M.eigenvects()
        parts = []
        for eigenval, mult, vects in eigenvects:
            vect_strs = [format_result(v) for v in vects]
            parts.append(f"特征值 {format_result(eigenval)} (重数 {mult}): {', '.join(vect_strs)}")
        return "\n".join(parts)
    return run_with_timeout(_compute, timeout=15.0)


@tool
def matrix_rank(matrix: str) -> str:
    """计算矩阵的秩。

    Args:
        matrix: 矩阵，JSON 格式

    Returns:
        矩阵的秩
    """
    def _compute():
        M = parse_matrix(matrix)
        if M is None:
            return f"[Error: cannot parse matrix '{matrix}']"
        result = M.rank()
        return str(result)
    return run_with_timeout(_compute)


@tool
def matrix_rref(matrix: str) -> str:
    """计算矩阵的行最简阶梯形（Reduced Row Echelon Form）。

    Args:
        matrix: 矩阵，JSON 格式

    Returns:
        行最简形矩阵及主元列索引
    """
    def _compute():
        M = parse_matrix(matrix)
        if M is None:
            return f"[Error: cannot parse matrix '{matrix}']"
        rref_mat, pivot_cols = M.rref()
        return f"{format_result(rref_mat)}\n主元列: {list(pivot_cols)}"
    return run_with_timeout(_compute, timeout=10.0)


@tool
def matrix_transpose(matrix: str) -> str:
    """计算矩阵的转置。

    Args:
        matrix: 矩阵，JSON 格式

    Returns:
        转置矩阵
    """
    def _compute():
        M = parse_matrix(matrix)
        if M is None:
            return f"[Error: cannot parse matrix '{matrix}']"
        result = M.T
        return format_result(result)
    return run_with_timeout(_compute)


@tool
def matrix_trace(matrix: str) -> str:
    """计算方阵的迹（主对角线元素之和）。

    Args:
        matrix: 方阵，JSON 格式

    Returns:
        矩阵的迹
    """
    def _compute():
        M = parse_matrix(matrix)
        if M is None:
            return f"[Error: cannot parse matrix '{matrix}']"
        if M.rows != M.cols:
            return f"[Error: matrix is not square: {M.shape}]"
        result = M.trace()
        return format_result(result)
    return run_with_timeout(_compute)


@tool
def solve_linear_system(augmented_matrix: str) -> str:
    """求解线性方程组。输入增广矩阵 [A|b]，返回解向量。

    Args:
        augmented_matrix: 增广矩阵，JSON 格式如 '[[1,2,3],[4,5,6]]' 表示 x+2y=3, 4x+5y=6

    Returns:
        方程组的解
    """
    def _compute():
        M = parse_matrix(augmented_matrix)
        if M is None:
            return f"[Error: cannot parse augmented_matrix '{augmented_matrix}']"
        A = M[:, :-1]
        b = M[:, -1]
        result = A.gauss_jordan_solve(b)
        return format_result(result)
    return run_with_timeout(_compute, timeout=10.0)
