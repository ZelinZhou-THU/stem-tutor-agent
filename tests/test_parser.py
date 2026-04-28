from stem_tutor.nodes.parse_student_solution import make_parse_student_solution_node
from stem_tutor.providers.mock_provider import MockProvider


_parse_node = make_parse_student_solution_node(MockProvider())


def test_parse_student_solution_basic():
    state = {
        "raw_student_solution": "1) a = b\n2) b = c",
        "trace": [],
    }
    out = _parse_node(state)
    assert len(out["normalized_steps"]) == 2
    assert out["normalized_steps"][0].step_id == "S1"
    assert "fail_reason" not in out


def test_parse_student_solution_empty():
    state = {"raw_student_solution": "\n\n", "trace": []}
    out = _parse_node(state)
    assert out.get("fail_reason") == "No parseable student steps"
