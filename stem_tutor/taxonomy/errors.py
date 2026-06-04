from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TaxonomyEntry:
    code: str
    category: str
    short_desc: str
    cues: tuple[str, ...]


def _default_fallback() -> dict[str, TaxonomyEntry]:
    return {
        "CHAIN_RULE_MISUSE": TaxonomyEntry(
            code="CHAIN_RULE_MISUSE",
            category="Rule Application Errors",
            short_desc="链式法则应用错误。",
            cues=("composition", "inner derivative", "chain"),
        ),
        "SUBSTITUTION_MAPPING_MISMATCH": TaxonomyEntry(
            code="SUBSTITUTION_MAPPING_MISMATCH",
            category="Rule Application Errors",
            short_desc="变量代换前后不一致。",
            cues=("u-sub", "substitution", "variable mismatch"),
        ),
        "PRODUCT_RULE_MISUSE": TaxonomyEntry(
            code="PRODUCT_RULE_MISUSE",
            category="Differentiation Errors",
            short_desc="乘法法则 (uv)' = u'v + uv' 应用错误。",
            cues=("product rule", "product", "multiplication", "乘法法则"),
        ),
        "QUOTIENT_RULE_MISUSE": TaxonomyEntry(
            code="QUOTIENT_RULE_MISUSE",
            category="Differentiation Errors",
            short_desc="除法法则 (u/v)' 应用错误。",
            cues=("quotient rule", "division", "quotient", "除法法则"),
        ),
        "IMPLICIT_DIFFERENTIATION_ERROR": TaxonomyEntry(
            code="IMPLICIT_DIFFERENTIATION_ERROR",
            category="Differentiation Errors",
            short_desc="隐函数求导时遗漏了 dy/dx 项。",
            cues=("implicit", "dy/dx", "隐函数"),
        ),
        "INTEGRATION_BOUNDARY_ERROR": TaxonomyEntry(
            code="INTEGRATION_BOUNDARY_ERROR",
            category="Integration Errors",
            short_desc="定积分换元后未更新积分限。",
            cues=("boundary", "limit", "积分限", "换元", "substitution"),
        ),
        "INTEGRATION_BY_PARTS_WRONG_ASSIGNMENT": TaxonomyEntry(
            code="INTEGRATION_BY_PARTS_WRONG_ASSIGNMENT",
            category="Integration Errors",
            short_desc="分部积分中 u 和 dv 的选取不当。",
            cues=("integration by parts", "分部积分", "u dv"),
        ),
        "PARTIAL_FRACTIONS_DECOMPOSITION_ERROR": TaxonomyEntry(
            code="PARTIAL_FRACTIONS_DECOMPOSITION_ERROR",
            category="Integration Errors",
            short_desc="部分分式分解形式错误。",
            cues=("partial fraction", "部分分式", "decomposition"),
        ),
        "INTEGRATION_CONSTANT_OMISSION": TaxonomyEntry(
            code="INTEGRATION_CONSTANT_OMISSION",
            category="Algebraic Manipulation Errors",
            short_desc="不定积分遗漏了常数 C。",
            cues=("constant", "+C", "constant of integration"),
        ),
        "SIGN_ARITHMETIC_ERROR": TaxonomyEntry(
            code="SIGN_ARITHMETIC_ERROR",
            category="Algebraic Manipulation Errors",
            short_desc="符号或算术化简错误。",
            cues=("minus", "sign", "simplify"),
        ),
        "DOMAIN_CONDITION_IGNORED": TaxonomyEntry(
            code="DOMAIN_CONDITION_IGNORED",
            category="Theorem/Condition Misuse",
            short_desc="忽略了定义域或定理前提条件。",
            cues=("domain", "condition", "precondition"),
        ),
        "OBJECT_CONFUSION_LIMIT_DERIVATIVE_INTEGRAL": TaxonomyEntry(
            code="OBJECT_CONFUSION_LIMIT_DERIVATIVE_INTEGRAL",
            category="Conceptual Confusion",
            short_desc="混淆了极限/导数/积分的概念。",
            cues=("limit", "derivative", "integral", "concept"),
        ),
        "UNSUPPORTED_JUMP": TaxonomyEntry(
            code="UNSUPPORTED_JUMP",
            category="Reasoning Quality Issues",
            short_desc="步骤缺乏充分的推理依据。",
            cues=("therefore", "obvious", "skip"),
        ),
        "COEFFICIENT_OMISSION": TaxonomyEntry(
            code="COEFFICIENT_OMISSION",
            category="Algebraic Manipulation Errors",
            short_desc="遗漏了系数或常数因子。",
            cues=("coefficient", "factor", "missing", "omitted"),
        ),
        "FINAL_CALCULATION_ERROR": TaxonomyEntry(
            code="FINAL_CALCULATION_ERROR",
            category="Algebraic Manipulation Errors",
            short_desc="最终数值计算错误。",
            cues=("arithmetic", "calculation", "numeric"),
        ),
        "TRANSCRIPTION_ERROR": TaxonomyEntry(
            code="TRANSCRIPTION_ERROR",
            category="Algebraic Manipulation Errors",
            short_desc="抄写或转录错误，如抄漏系数/符号/指数。",
            cues=("transcription", "copy", "omit", "missing digit", "scribal"),
        ),
        "NOTATION_UNCLEAR": TaxonomyEntry(
            code="NOTATION_UNCLEAR",
            category="Reasoning Quality Issues",
            short_desc="符号表达模糊或不清晰。",
            cues=("unclear", "ambiguous", "notation"),
        ),
    }


def get_effective_taxonomy(subject_id: str = "calculus") -> dict[str, TaxonomyEntry]:
    try:
        from stem_tutor.subjects.context import get_subject_context
        ctx = get_subject_context(subject_id)
        return ctx.error_taxonomy
    except Exception:
        return _default_fallback()


# Kept for backward compatibility; defaults to calculus.
ERROR_TAXONOMY: dict[str, TaxonomyEntry] = get_effective_taxonomy("calculus")


def lookup_error(code: str, subject_id: str | None = None) -> TaxonomyEntry | None:
    if subject_id is None:
        return ERROR_TAXONOMY.get(code)
    return get_effective_taxonomy(subject_id).get(code)
