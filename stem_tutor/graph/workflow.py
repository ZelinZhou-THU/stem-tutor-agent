from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4
from typing import Literal

from langgraph.graph import END, START, StateGraph

from stem_tutor.domain.models import ProblemInput, VerificationLabel
from stem_tutor.graph.state import TutorGraphState
from stem_tutor.nodes.finalize_report import finalize_report_node
from stem_tutor.nodes.generate_feedback import make_generate_feedback_node
from stem_tutor.nodes.generate_reference_solution import make_generate_reference_solution_node
from stem_tutor.nodes.generate_review_problems import make_generate_review_problems_node
from stem_tutor.nodes.parse_student_solution import make_parse_student_solution_node
from stem_tutor.nodes.verify_steps import make_verify_steps_node
from stem_tutor.nodes.diagnose_error import make_diagnose_error_node
from stem_tutor.providers.base import LLMProvider


StartRoute = Literal["to_parse", "to_ocr"]
ParseRoute = Literal["continue", "stop"]
VerifyRoute = Literal["low_conf_stop", "need_diagnosis", "skip_diagnosis"]


def _route_after_start(state: TutorGraphState) -> StartRoute:
    problem = state["problem_input"]
    if problem.source_type == "ocr":
        return "to_ocr"
    return "to_parse"


def _route_after_parse(state: TutorGraphState) -> ParseRoute:
    if state.get("fail_reason"):
        return "stop"
    return "continue"


def _route_after_verify(state: TutorGraphState) -> VerifyRoute:
    flags = set(state.get("uncertainty_flags", []))
    if "too_many_low_confidence_steps" in flags:
        return "low_conf_stop"

    results = state.get("verification_results", [])
    if any(r.label != VerificationLabel.CORRECT for r in results):
        return "need_diagnosis"
    return "skip_diagnosis"


def build_tutor_graph(
    provider: LLMProvider,
    ocr_provider: LLMProvider | None = None,
    fast_provider: LLMProvider | None = None,
    verify_provider: LLMProvider | None = None,
):
    effective_ocr_provider = ocr_provider or provider
    effective_fast_provider = fast_provider or provider
    effective_verify_provider = verify_provider or provider
    graph = StateGraph(TutorGraphState)

    from stem_tutor.nodes.ocr_preprocess import make_ocr_preprocess_node

    graph.add_node("ocr_preprocess", make_ocr_preprocess_node(effective_ocr_provider))
    graph.add_node("parse_student_solution", make_parse_student_solution_node(effective_fast_provider))
    graph.add_node("generate_reference_solution", make_generate_reference_solution_node(provider))
    graph.add_node("verify_steps", make_verify_steps_node(effective_verify_provider))
    graph.add_node("diagnose_error", make_diagnose_error_node(effective_fast_provider))
    graph.add_node("generate_feedback", make_generate_feedback_node(effective_fast_provider))
    graph.add_node("generate_review_problems", make_generate_review_problems_node(effective_fast_provider))
    graph.add_node("finalize_report", finalize_report_node)

    graph.add_conditional_edges(
        START,
        _route_after_start,
        {
            "to_parse": "parse_student_solution",
            "to_ocr": "ocr_preprocess",
        },
    )
    graph.add_edge("ocr_preprocess", "parse_student_solution")
    graph.add_conditional_edges(
        "parse_student_solution",
        _route_after_parse,
        {
            "continue": "generate_reference_solution",
            "stop": "finalize_report",
        },
    )
    graph.add_edge("generate_reference_solution", "verify_steps")
    graph.add_conditional_edges(
        "verify_steps",
        _route_after_verify,
        {
            "low_conf_stop": "finalize_report",
            "need_diagnosis": "diagnose_error",
            "skip_diagnosis": "generate_feedback",
        },
    )
    graph.add_edge("diagnose_error", "generate_feedback")
    graph.add_edge("generate_feedback", "generate_review_problems")
    graph.add_edge("generate_review_problems", "finalize_report")
    graph.add_edge("finalize_report", END)

    return graph.compile()


def run_tutor_graph(
    provider: LLMProvider,
    problem_input: ProblemInput,
    raw_student_solution: str,
    ocr_provider: LLMProvider | None = None,
    fast_provider: LLMProvider | None = None,
    verify_provider: LLMProvider | None = None,
) -> TutorGraphState:
    app = build_tutor_graph(provider, ocr_provider=ocr_provider, fast_provider=fast_provider, verify_provider=verify_provider)
    provider_info = provider.provider_info()
    ocr_provider_info = (ocr_provider or provider).provider_info()
    fast_provider_info = (fast_provider or provider).provider_info()
    verify_provider_info = (verify_provider or provider).provider_info()
    initial_state: TutorGraphState = {
        "problem_input": problem_input,
        "raw_student_solution": raw_student_solution,
        "trace": [],
        "run_meta": {
            "run_id": str(uuid4()),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "provider": provider_info.get("provider_name", "unknown"),
            "model": provider_info.get("model_name", "unknown"),
            "fast_model": fast_provider_info.get("model_name", "unknown"),
            "ocr_model": ocr_provider_info.get("model_name", "unknown"),
            "verify_model": verify_provider_info.get("model_name", "unknown"),
            "workflow_version": "v1",
            "node_stats": {},
            "provider_events": [],
        },
    }
    return app.invoke(initial_state)
