"""Chinese error_type -> taxonomy code mapping."""

from dataclasses import dataclass


@dataclass
class MappedError:
    error_code: str
    category: str
    confidence: str
    note: str = ""


_MAPPING: dict[str, MappedError] = {
    "\u7b97\u672f\u9519\u8bef": MappedError(
        error_code="FINAL_CALCULATION_ERROR",
        category="Algebraic Manipulation Errors",
        confidence="high",
        note="\u7b97\u672f\u8ba1\u7b97\u4e2d\u7684\u6700\u7ec8\u7ed3\u679c\u9519\u8bef",
    ),
    "\u8ba1\u7b97\u9519\u8bef-\u6f0f\u4e58\u7cfb\u6570": MappedError(
        error_code="COEFFICIENT_OMISSION",
        category="Algebraic Manipulation Errors",
        confidence="high",
        note="\u5316\u7b80\u8fc7\u7a0b\u4e2d\u6f0f\u4e58\u7cfb\u6570",
    ),
    "\u6982\u5ff5\u9057\u6f0f-\u5ffd\u7565\u5947\u70b9": MappedError(
        error_code="DOMAIN_CONDITION_IGNORED",
        category="Theorem/Condition Misuse",
        confidence="medium",
        note="\u5ffd\u7565\u5b9a\u4e49\u57df\u5185\u7684\u5947\u70b9/\u4e0d\u53ef\u5bfc\u70b9",
    ),
    "\u5b9a\u7406\u8bef\u7528-\u6d1b\u5fc5\u8fbe\u6cd5\u5219\u6761\u4ef6\u4e0d\u6ee1\u8db3": MappedError(
        error_code="DOMAIN_CONDITION_IGNORED",
        category="Theorem/Condition Misuse",
        confidence="medium",
        note="\u4e0d\u6ee1\u8db3\u6d1b\u5fc5\u8fbe\u6cd5\u5219\u6761\u4ef6",
    ),
    "\u8bc1\u660e\u4e0d\u4e25\u8c28-\u9057\u6f0f\u6b65\u9aa4": MappedError(
        error_code="UNSUPPORTED_JUMP",
        category="Reasoning Quality Issues",
        confidence="low",
        note="\u8bc1\u660e\u4e2d\u9057\u6f0f\u5173\u952e\u6b65\u9aa4",
    ),
}

_EMPTY = MappedError(error_code="", category="", confidence="none")


def map_error_type(error_type: str) -> MappedError:
    if not error_type or not error_type.strip():
        return _EMPTY
    return _MAPPING.get(
        error_type.strip(),
        MappedError(error_code="", category="", confidence="none", note=f"\u672a\u77e5\u9519\u8bef\u7c7b\u578b: {error_type}"),
    )
