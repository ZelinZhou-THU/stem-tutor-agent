from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from stem_tutor.graph.state import TutorGraphState
from stem_tutor.providers.base import LLMProvider


def _append_flag(flags: list[str], flag: str) -> None:
    if flag not in flags:
        flags.append(flag)


def record_provider_call(
    state: TutorGraphState,
    provider: LLMProvider,
    node_name: str,
    fallback_flag: str,
    local_schema_fallback: bool = False,
    started_at: float | None = None,
) -> tuple[list[str], dict[str, Any]]:
    flags = list(state.get("uncertainty_flags", []))
    run_meta = dict(state.get("run_meta", {}))
    meta = provider.get_last_call_meta()

    retries = int(meta.get("retries", 0))
    used_fallback = bool(meta.get("used_fallback", False))
    error_type = meta.get("error_type")

    node_stats = dict(run_meta.get("node_stats", {}))
    stat = dict(node_stats.get(node_name, {}))
    stat["provider_calls"] = int(stat.get("provider_calls", 0)) + 1
    stat["fallback_calls"] = int(stat.get("fallback_calls", 0)) + int(used_fallback or local_schema_fallback)
    stat["retry_sum"] = int(stat.get("retry_sum", 0)) + retries
    if started_at is not None:
        import time
        stat["last_elapsed_ms"] = int((time.perf_counter() - started_at) * 1000)
    node_stats[node_name] = stat
    run_meta["node_stats"] = node_stats

    events = list(run_meta.get("provider_events", []))
    event: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "node": node_name,
        "error_type": error_type,
        "retries": retries,
        "used_fallback": used_fallback or local_schema_fallback,
    }
    if started_at is not None:
        import time
        event["elapsed_ms"] = int((time.perf_counter() - started_at) * 1000)
    events.append(event)
    run_meta["provider_events"] = events

    if used_fallback or local_schema_fallback:
        _append_flag(flags, fallback_flag)

    if local_schema_fallback:
        _append_flag(flags, f"{node_name}_schema_validation")

    if error_type:
        _append_flag(flags, f"{node_name}_provider_{error_type}")

    return flags, run_meta
