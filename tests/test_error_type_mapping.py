from stem_tutor.evaluation.error_type_mapping import map_error_type


def test_map_arithmetic_error():
    result = map_error_type("\u7b97\u672f\u9519\u8bef")
    assert result.error_code == "FINAL_CALCULATION_ERROR"
    assert result.category == "Algebraic Manipulation Errors"
    assert result.confidence == "high"


def test_map_coefficient_omission():
    result = map_error_type("\u8ba1\u7b97\u9519\u8bef-\u6f0f\u4e58\u7cfb\u6570")
    assert result.error_code == "COEFFICIENT_OMISSION"
    assert result.confidence == "high"


def test_map_singularity_ignored():
    result = map_error_type("\u6982\u5ff5\u9057\u6f0f-\u5ffd\u7565\u5947\u70b9")
    assert result.error_code == "DOMAIN_CONDITION_IGNORED"
    assert result.confidence == "medium"


def test_map_lhopital_misuse():
    result = map_error_type("\u5b9a\u7406\u8bef\u7528-\u6d1b\u5fc5\u8fbe\u6cd5\u5219\u6761\u4ef6\u4e0d\u6ee1\u8db3")
    assert result.error_code == "DOMAIN_CONDITION_IGNORED"
    assert result.confidence == "medium"


def test_map_proof_gap():
    result = map_error_type("\u8bc1\u660e\u4e0d\u4e25\u8c28-\u9057\u6f0f\u6b65\u9aa4")
    assert result.error_code == "UNSUPPORTED_JUMP"
    assert result.confidence == "low"


def test_map_unknown_error_type():
    result = map_error_type("\u5b8c\u5168\u672a\u77e5\u7684\u9519\u8bef\u7c7b\u578b")
    assert result.error_code == ""
    assert result.confidence == "none"


def test_map_empty_error_type_for_correct():
    result = map_error_type("")
    assert result.error_code == ""
    assert result.confidence == "none"
