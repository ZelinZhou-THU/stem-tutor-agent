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


def _get_taxonomy() -> dict[str, TaxonomyEntry]:
    try:
        from stem_tutor.subjects.context import get_subject_context
        ctx = get_subject_context()
        return ctx.error_taxonomy
    except Exception:
        return _default_fallback()


ERROR_TAXONOMY: dict[str, TaxonomyEntry] = _get_taxonomy()


def lookup_error(code: str) -> TaxonomyEntry | None:
    return ERROR_TAXONOMY.get(code)
