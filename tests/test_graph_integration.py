from stem_tutor.domain.models import ProblemInput
from stem_tutor.graph.workflow import run_tutor_graph
from stem_tutor.providers.mock_provider import MockProvider


def test_graph_runs_end_to_end():
    provider = MockProvider()
    problem = ProblemInput(
        problem_id="it-001",
        problem_text="Differentiate y = sin(x^2)",
        topic_tags=["derivative"],
    )
    raw_solution = "y' = cos(x^2)\ntherefore done"

    out = run_tutor_graph(provider, problem, raw_solution)

    assert "verification_results" in out
    assert "final_feedback" in out
    assert "review_problems" in out
    assert out["run_meta"]["failed"] is False
