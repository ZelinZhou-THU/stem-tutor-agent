from stem_tutor.domain.models import ProblemInput
from stem_tutor.graph.workflow import run_tutor_graph
from stem_tutor.providers.mock_provider import MockProvider


def test_ocr_source_routes_through_ocr_preprocess():
    reasoning_provider = MockProvider(model_group="reasoning", model_name="mock-DeepSeek-R1-0528")
    ocr_provider = MockProvider(model_group="ocr", model_name="mock-PaddleOCR-VL-1.5")

    problem = ProblemInput(
        problem_id="ocr-001",
        problem_text="Differentiate y = sin(x^2)",
        source_type="ocr",
        ocr_payload="fake_image_payload",
        topic_tags=["derivative"],
    )

    out = run_tutor_graph(
        reasoning_provider,
        problem,
        raw_student_solution="",
        ocr_provider=ocr_provider,
    )

    assert "ocr_source_input" in set(out.get("uncertainty_flags", []))
    assert out.get("normalized_steps")
    assert out["run_meta"]["ocr_model"] == "mock-PaddleOCR-VL-1.5"
