from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from stem_tutor.graph.state import TutorGraphState


def _write_run_artifact(run_meta: dict, tool_calls_log: list[dict]) -> str | None:
    run_id = run_meta.get("run_id")
    if not run_id:
        return None

    project_root = Path(__file__).resolve().parents[2]
    out_dir = project_root / "logs" / "runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{run_id}.jsonl"

    provider_events = run_meta.get("provider_events", [])
    trace = run_meta.get("trace_snapshot", [])

    with out_path.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "run_meta", "run_meta": run_meta}, ensure_ascii=False) + "\n")
        for event in provider_events:
            f.write(json.dumps({"type": "provider_event", **event}, ensure_ascii=False) + "\n")
        for idx, item in enumerate(trace, start=1):
            f.write(json.dumps({"type": "trace", "index": idx, "message": item}, ensure_ascii=False) + "\n")
        for tc in tool_calls_log:
            f.write(json.dumps({"type": "tool_call", **tc}, ensure_ascii=False) + "\n")

    return str(out_path)


def finalize_report_node(state: TutorGraphState) -> TutorGraphState:
    trace = state.get("trace", [])
    trace.append("finalize_report: done")
    existing_meta = state.get("run_meta", {})
    uncertainty_flags = state.get("uncertainty_flags", [])
    provider_events = existing_meta.get("provider_events", [])
    tool_calls_log = state.get("tool_calls_log", [])

    tool_call_stats = {
        "total_calls": len(tool_calls_log),
        "by_node": {},
        "by_tool": {},
    }
    for tc in tool_calls_log:
        node = tc.get("node", "unknown")
        tool = tc.get("tool_name", "unknown")
        tool_call_stats["by_node"][node] = tool_call_stats["by_node"].get(node, 0) + 1
        tool_call_stats["by_tool"][tool] = tool_call_stats["by_tool"].get(tool, 0) + 1

    merged_meta = {
        **existing_meta,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "failed": bool(state.get("fail_reason")),
        "trace_entries": len(trace),
        "uncertainty_flag_count": len(uncertainty_flags),
        "provider_event_count": len(provider_events),
        "trace_snapshot": trace,
        "tool_call_stats": tool_call_stats,
    }

    try:
        from stem_tutor.settings import is_node_timing_enabled
        if is_node_timing_enabled():
            latency_by_node: dict[str, int] = {}
            node_stats = existing_meta.get("node_stats", {})
            for node_name, stats in node_stats.items():
                elapsed = stats.get("last_elapsed_ms")
                if elapsed is not None:
                    latency_by_node[node_name] = elapsed
            started_str = existing_meta.get("started_at")
            total_ms = None
            if started_str:
                try:
                    from datetime import datetime as _dt
                    started_dt = _dt.fromisoformat(started_str)
                    total_ms = int((datetime.now(timezone.utc) - started_dt).total_seconds() * 1000)
                except (ValueError, TypeError):
                    pass
            merged_meta["latency_summary"] = {
                "total_ms": total_ms,
                "by_node": latency_by_node,
            }
    except Exception:
        pass

    artifact_path = _write_run_artifact(merged_meta, tool_calls_log)
    if artifact_path:
        merged_meta["artifact_path"] = artifact_path

    return {
        "trace": trace,
        "run_meta": merged_meta,
    }
