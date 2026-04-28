from pathlib import Path
import json

from stem_tutor.domain.models import ProblemInput
from stem_tutor.graph.workflow import run_tutor_graph
from stem_tutor.providers.mock_provider import MockProvider


def test_provider_info_exists():
    info = MockProvider().provider_info()
    assert info["provider_name"] == "mock"
    assert "model_name" in info


def test_run_meta_contains_provider_and_timing():
    provider = MockProvider()
    problem = ProblemInput(
        problem_id="meta-001",
        problem_text="Differentiate y = x^2",
        topic_tags=["derivative"],
    )
    out = run_tutor_graph(provider, problem, "1) y' = 2x")

    meta = out["run_meta"]
    assert meta["provider"] == "mock"
    assert "run_id" in meta
    assert "started_at" in meta
    assert "completed_at" in meta
    assert "trace_entries" in meta
    assert "uncertainty_flag_count" in meta
    assert "provider_event_count" in meta
    assert isinstance(meta.get("node_stats"), dict)
    assert meta["provider_event_count"] >= 1
    assert meta["node_stats"].get("reference", {}).get("provider_calls", 0) >= 1
    assert meta["node_stats"].get("verify", {}).get("provider_calls", 0) >= 1

    # Mock flow should still produce an artifact file for debugging and audit.
    artifact_path = meta.get("artifact_path")
    assert artifact_path
    artifact = Path(artifact_path)
    assert artifact.exists()

    lines = [json.loads(line) for line in artifact.read_text(encoding="utf-8").splitlines()]
    assert lines
    assert lines[0]["type"] == "run_meta"
    assert any(item.get("type") == "provider_event" for item in lines)
    assert any(item.get("type") == "trace" for item in lines)
