"""Tests for report_stream heartbeat and retry behavior.

Covers:
- Initial progress messages are emitted
- 5-second heartbeat is emitted when LLM is slow (no chunks for >5s)
- Chunk-based progress message fires when LLM streams >=500 chars
- Retry on 5xx HTTP status
- Retry on requests.Timeout / ConnectionError
- Retry on empty response (no chunks before thread_end)
- No retry on 4xx (except 429)
- Full success yields report_section events with valid JSON
- Non-JSON response yields report_error
"""
import asyncio
import json
import time
from unittest.mock import MagicMock, patch, AsyncMock

import pytest


# ----- helpers -----

VALID_SECTIONS_JSON = {
    "sections": [
        {
            "type": "error_patterns",
            "title": "错误模式识别",
            "icon": "🔍",
            "summary": "测试摘要",
            "items": [],
        }
    ]
}


def _make_data():
    return {
        "time_range": {"start": "2025-01-01", "end": "2026-06-06", "days": 30},
        "total_runs": 5,
        "error_frequency": [],
        "radar_data": {},
        "heatmap_data": {},
        "error_evolution": [],
        "improvement_signals": [],
        "taxonomy_summary": {},
        "mastery_summary": None,
        "error_examples": {},
        "resolved_summary": None,
    }


def _sse_chunk(content):
    """Encode a streaming chunk as the LLM would send."""
    return f"data: {json.dumps({'choices': [{'delta': {'content': content}}]})}\n\n".encode("utf-8")


def _parse_sse(chunk):
    """Extract the JSON payload from a `data: ...` SSE line. Returns None for non-data lines."""
    if not chunk.startswith("data:"):
        return None
    return json.loads(chunk[5:].strip())


async def _collect(gen, max_events=20, timeout=15):
    """Collect events from an async generator with a timeout."""
    events = []
    start = time.time()

    async def _runner():
        async for chunk in gen:
            if chunk.startswith("data:"):
                ev = _parse_sse(chunk)
                if ev is not None:
                    events.append((time.time() - start, ev))
                    if len(events) >= max_events:
                        return

    try:
        await asyncio.wait_for(_runner(), timeout=timeout)
    except asyncio.TimeoutError:
        pass
    return events


def _fake_response(lines, status_code=200):
    """Build a fake requests.Response that yields the given byte lines."""

    class _Resp:
        def __init__(self):
            self.status_code = status_code

        def iter_lines(self):
            return iter(lines)

        def raise_for_status(self):
            if self.status_code != 200:
                import requests
                raise requests.HTTPError(f"{self.status_code} error", response=self)

        def close(self):
            pass

    return _Resp()


# ----- tests -----


def test_initial_progress_emitted():
    """The first SSE event is report_progress: 正在准备数据并调用 AI 模型..."""
    from web import service

    with patch("web.service.requests.post") as mock_post, \
         patch("web.service.load_provider_settings", return_value=MagicMock(base_url="http://x", api_key="y")):
        mock_post.return_value = _fake_response([_sse_chunk("x"), b"data: [DONE]\n\n"])
        gen = service.report_stream(user_id=1, data=_make_data(), model_name="test")

        async def _run():
            return await _collect(gen, max_events=3, timeout=10)

        events = asyncio.run(_run())
    assert len(events) >= 1
    t, ev = events[0]
    assert ev["type"] == "report_progress"
    assert "正在准备数据" in ev["message"]


def test_calling_llm_progress_emitted():
    """Second event is report_progress: 正在调用 AI 模型..."""
    from web import service

    with patch("web.service.requests.post") as mock_post, \
         patch("web.service.load_provider_settings", return_value=MagicMock(base_url="http://x", api_key="y")):
        mock_post.return_value = _fake_response([_sse_chunk("x"), b"data: [DONE]\n\n"])
        gen = service.report_stream(user_id=1, data=_make_data(), model_name="test")

        async def _run():
            return await _collect(gen, max_events=3, timeout=10)

        events = asyncio.run(_run())
    assert len(events) >= 2
    t, ev = events[1]
    assert ev["type"] == "report_progress"
    assert "正在调用 AI 模型" in ev["message"]


def test_heartbeat_emitted_during_slow_llm():
    """When the LLM takes >5s to return any chunk, a heartbeat is emitted."""
    from web import service

    def slow_iter():
        time.sleep(7)
        yield _sse_chunk("hello")
        yield b"data: [DONE]\n\n"

    with patch("web.service.requests.post") as mock_post, \
         patch("web.service.load_provider_settings", return_value=MagicMock(base_url="http://x", api_key="y")):
        mock_post.return_value = _fake_response(slow_iter())
        gen = service.report_stream(user_id=1, data=_make_data(), model_name="test")

        async def _run():
            return await _collect(gen, max_events=8, timeout=12)

        events = asyncio.run(_run())

    heartbeat_events = [
        (t, ev) for t, ev in events
        if ev.get("type") == "report_progress" and "已等待" in ev.get("message", "")
    ]
    assert len(heartbeat_events) >= 1, f"Expected at least 1 heartbeat, got events: {events}"
    t_first, ev_first = heartbeat_events[0]
    assert 4.0 < t_first < 8.0, f"First heartbeat should be around 5s, got t={t_first}"


def test_chunk_progress_emitted_on_500_char_crossing():
    """When LLM streams >=500 chars in one chunk, AI 模型返回中 progress fires."""
    from web import service

    big = "x" * 600
    lines = [_sse_chunk(big), b"data: [DONE]\n\n"]

    with patch("web.service.requests.post") as mock_post, \
         patch("web.service.load_provider_settings", return_value=MagicMock(base_url="http://x", api_key="y")), \
         patch("web.service._save_report", new=AsyncMock()):
        mock_post.return_value = _fake_response(lines)
        gen = service.report_stream(user_id=1, data=_make_data(), model_name="test")

        async def _run():
            return await _collect(gen, max_events=15, timeout=10)

        events = asyncio.run(_run())

    chunk_progress = [
        (t, ev) for t, ev in events
        if ev.get("type") == "report_progress" and "已接收" in ev.get("message", "")
    ]
    assert len(chunk_progress) >= 1
    assert "600" in chunk_progress[0][1]["message"]


def test_retry_on_500():
    """First attempt returns 500, second attempt succeeds."""
    from web import service

    call_count = {"n": 0}

    def side_effect(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _fake_response([], status_code=500)
        return _fake_response([_sse_chunk(json.dumps(VALID_SECTIONS_JSON)), b"data: [DONE]\n\n"])

    with patch("web.service.requests.post", side_effect=side_effect), \
         patch("web.service.load_provider_settings", return_value=MagicMock(base_url="http://x", api_key="y")), \
         patch("web.service._save_report", new=AsyncMock()):
        gen = service.report_stream(user_id=1, data=_make_data(), model_name="test")

        async def _run():
            return await _collect(gen, max_events=20, timeout=10)

        events = asyncio.run(_run())

    assert call_count["n"] == 2
    retry_events = [ev for _, ev in events if ev.get("type") == "report_progress" and "准备重试" in ev.get("message", "")]
    assert len(retry_events) >= 1
    section_events = [ev for _, ev in events if ev.get("type") == "report_section"]
    assert len(section_events) == 1


def test_retry_on_429():
    """First attempt returns 429, second attempt succeeds."""
    from web import service

    call_count = {"n": 0}

    def side_effect(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _fake_response([], status_code=429)
        return _fake_response([_sse_chunk(json.dumps(VALID_SECTIONS_JSON)), b"data: [DONE]\n\n"])

    with patch("web.service.requests.post", side_effect=side_effect), \
         patch("web.service.load_provider_settings", return_value=MagicMock(base_url="http://x", api_key="y")), \
         patch("web.service._save_report", new=AsyncMock()):
        gen = service.report_stream(user_id=1, data=_make_data(), model_name="test")

        async def _run():
            return await _collect(gen, max_events=20, timeout=10)

        events = asyncio.run(_run())

    assert call_count["n"] == 2
    section_events = [ev for _, ev in events if ev.get("type") == "report_section"]
    assert len(section_events) == 1


def test_no_retry_on_400():
    """400 is not retried; only one POST and one report_error."""
    from web import service

    call_count = {"n": 0}

    def side_effect(*args, **kwargs):
        call_count["n"] += 1
        return _fake_response([], status_code=400)

    with patch("web.service.requests.post", side_effect=side_effect), \
         patch("web.service.load_provider_settings", return_value=MagicMock(base_url="http://x", api_key="y")), \
         patch("web.service._save_report", new=AsyncMock()):
        gen = service.report_stream(user_id=1, data=_make_data(), model_name="test")

        async def _run():
            return await _collect(gen, max_events=10, timeout=10)

        events = asyncio.run(_run())

    assert call_count["n"] == 1
    error_events = [ev for _, ev in events if ev.get("type") == "report_error"]
    assert len(error_events) == 1
    assert "400" in error_events[0]["message"]


def test_retry_on_timeout():
    """First attempt raises requests.Timeout, second succeeds."""
    from web import service
    import requests

    call_count = {"n": 0}

    def side_effect(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise requests.Timeout("simulated")
        return _fake_response([_sse_chunk(json.dumps(VALID_SECTIONS_JSON)), b"data: [DONE]\n\n"])

    with patch("web.service.requests.post", side_effect=side_effect), \
         patch("web.service.load_provider_settings", return_value=MagicMock(base_url="http://x", api_key="y")), \
         patch("web.service._save_report", new=AsyncMock()):
        gen = service.report_stream(user_id=1, data=_make_data(), model_name="test")

        async def _run():
            return await _collect(gen, max_events=20, timeout=10)

        events = asyncio.run(_run())

    assert call_count["n"] == 2
    section_events = [ev for _, ev in events if ev.get("type") == "report_section"]
    assert len(section_events) == 1


def test_retry_on_empty_response():
    """First attempt yields no chunks (empty stream), second succeeds."""
    from web import service

    call_count = {"n": 0}

    def side_effect(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _fake_response([])
        return _fake_response([_sse_chunk(json.dumps(VALID_SECTIONS_JSON)), b"data: [DONE]\n\n"])

    with patch("web.service.requests.post", side_effect=side_effect), \
         patch("web.service.load_provider_settings", return_value=MagicMock(base_url="http://x", api_key="y")), \
         patch("web.service._save_report", new=AsyncMock()):
        gen = service.report_stream(user_id=1, data=_make_data(), model_name="test")

        async def _run():
            return await _collect(gen, max_events=20, timeout=10)

        events = asyncio.run(_run())

    assert call_count["n"] == 2
    section_events = [ev for _, ev in events if ev.get("type") == "report_section"]
    assert len(section_events) == 1


def test_full_success_yields_sections():
    """Mock LLM returns valid JSON; report_section events are emitted."""
    from web import service

    full_response = json.dumps(VALID_SECTIONS_JSON, ensure_ascii=False)
    chunks = [_sse_chunk(full_response), b"data: [DONE]\n\n"]

    with patch("web.service.requests.post") as mock_post, \
         patch("web.service.load_provider_settings", return_value=MagicMock(base_url="http://x", api_key="y")), \
         patch("web.service._save_report", new=AsyncMock()):
        mock_post.return_value = _fake_response(chunks)
        gen = service.report_stream(user_id=1, data=_make_data(), model_name="test")

        async def _run():
            return await _collect(gen, max_events=20, timeout=10)

        events = asyncio.run(_run())

    section_events = [ev for _, ev in events if ev.get("type") == "report_section"]
    done_events = [ev for _, ev in events if ev.get("type") == "report_done"]
    assert len(section_events) == 1
    assert section_events[0]["section"]["type"] == "error_patterns"
    assert len(done_events) == 1
    assert "report_id" in done_events[0]


def test_fatal_json_parse_error():
    """Mock LLM returns non-JSON; report_error is emitted."""
    from web import service

    with patch("web.service.requests.post") as mock_post, \
         patch("web.service.load_provider_settings", return_value=MagicMock(base_url="http://x", api_key="y")), \
         patch("web.service._save_report", new=AsyncMock()):
        mock_post.return_value = _fake_response([_sse_chunk("not valid json"), b"data: [DONE]\n\n"])
        gen = service.report_stream(user_id=1, data=_make_data(), model_name="test")

        async def _run():
            return await _collect(gen, max_events=15, timeout=10)

        events = asyncio.run(_run())

    error_events = [ev for _, ev in events if ev.get("type") == "report_error"]
    assert len(error_events) == 1
    assert "JSON" in error_events[0]["message"] or "格式" in error_events[0]["message"]


def test_all_attempts_fail_returns_error():
    """3 attempts all fail; final report_error is emitted with no sections."""
    from web import service

    call_count = {"n": 0}

    def side_effect(*args, **kwargs):
        call_count["n"] += 1
        return _fake_response([], status_code=503)

    with patch("web.service.requests.post", side_effect=side_effect), \
         patch("web.service.load_provider_settings", return_value=MagicMock(base_url="http://x", api_key="y")), \
         patch("web.service._save_report", new=AsyncMock()):
        gen = service.report_stream(user_id=1, data=_make_data(), model_name="test")

        async def _run():
            return await _collect(gen, max_events=20, timeout=10)

        events = asyncio.run(_run())

    assert call_count["n"] == 3
    section_events = [ev for _, ev in events if ev.get("type") == "report_section"]
    error_events = [ev for _, ev in events if ev.get("type") == "report_error"]
    assert len(section_events) == 0
    assert len(error_events) == 1


def test_heartbeat_fires_even_with_frequent_chunks():
    """If the LLM sends small chunks frequently (e.g., every 1-2s with keep-alive
    comments or tiny content), the heartbeat should STILL fire every 5s based on
    wall-clock time, not queue emptiness. This is the regression test for the bug
    where '已等待 5 秒' was emitted once and then never updated.
    """
    from web import service

    def streaming_chunks():
        time.sleep(5.5)
        for _ in range(6):
            yield b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n'
            time.sleep(2.0)
        yield b"data: [DONE]\n\n"

    class _StreamingResp:
        status_code = 200

        def iter_lines(self):
            return streaming_chunks()

        def raise_for_status(self):
            pass

        def close(self):
            pass

    with patch("web.service.requests.post", return_value=_StreamingResp()), \
         patch("web.service.load_provider_settings", return_value=MagicMock(base_url="http://x", api_key="y")), \
         patch("web.service._save_report", new=AsyncMock()):
        gen = service.report_stream(user_id=1, data=_make_data(), model_name="test")

        async def _run():
            return await _collect(gen, max_events=20, timeout=20)

        events = asyncio.run(_run())

    heartbeat_events = [
        (t, ev) for t, ev in events
        if ev.get("type") == "report_progress" and "已等待" in ev.get("message", "")
    ]
    assert len(heartbeat_events) >= 2, (
        f"Expected >=2 heartbeats over ~17s of small-chunk streaming, got "
        f"{len(heartbeat_events)}: {heartbeat_events}"
    )
    t1, ev1 = heartbeat_events[0]
    t2, ev2 = heartbeat_events[1]
    assert 4.0 < t1 < 7.0, f"First heartbeat should be around 5-6s, got t={t1}"
    assert t2 - t1 >= 4.0, f"Heartbeats should be ~5s apart, got {t2 - t1:.2f}s"
    assert "5" in ev1["message"], f"First heartbeat should mention ~5s, got {ev1['message']}"


def test_heartbeat_fires_with_keep_alive_only():
    """If the LLM only sends keep-alive comment lines (no real data), heartbeat
    should still fire every 5s based on wall-clock time.
    """
    from web import service

    def keep_alive_stream():
        t0 = time.time()
        while time.time() - t0 < 12:
            yield b": keep-alive\n\n"
            time.sleep(1.0)
        yield b"data: [DONE]\n\n"

    class _KAResp:
        status_code = 200

        def iter_lines(self):
            return keep_alive_stream()

        def raise_for_status(self):
            pass

        def close(self):
            pass

    with patch("web.service.requests.post", return_value=_KAResp()), \
         patch("web.service.load_provider_settings", return_value=MagicMock(base_url="http://x", api_key="y")), \
         patch("web.service._save_report", new=AsyncMock()):
        gen = service.report_stream(user_id=1, data=_make_data(), model_name="test")

        async def _run():
            return await _collect(gen, max_events=20, timeout=18)

        events = asyncio.run(_run())

    heartbeat_events = [
        (t, ev) for t, ev in events
        if ev.get("type") == "report_progress" and "已等待" in ev.get("message", "")
    ]
    assert len(heartbeat_events) >= 2, (
        f"Expected >=2 heartbeats over 12s of keep-alive-only, got "
        f"{len(heartbeat_events)}: {heartbeat_events}"
    )


def test_chunk_with_empty_choices_does_not_crash():
    """LLM streaming responses may include chunks with `choices: []`
    (e.g. finish_reason-only chunks, tool-call finish chunks, or
    provider keep-alive frames). These must be skipped, not crash
    the SSE stream.
    """
    from web import service

    final_json = json.dumps(VALID_SECTIONS_JSON, ensure_ascii=False)
    lines = [
        b'data: {"id":"x","choices":[]}\n\n',
        b'data: {"id":"x","choices":[{"finish_reason":"length","index":0}]}\n\n',
        _sse_chunk(final_json),
        b'data: [DONE]\n\n',
    ]

    with patch("web.service.requests.post") as mock_post, \
         patch("web.service.load_provider_settings", return_value=MagicMock(base_url="http://x", api_key="y")), \
         patch("web.service._save_report", new=AsyncMock()):
        mock_post.return_value = _fake_response(lines)
        gen = service.report_stream(user_id=1, data=_make_data(), model_name="test")

        async def _run():
            return await _collect(gen, max_events=20, timeout=10)

        events = asyncio.run(_run())

    section_events = [ev for _, ev in events if ev.get("type") == "report_section"]
    done_events = [ev for _, ev in events if ev.get("type") == "report_done"]
    error_events = [ev for _, ev in events if ev.get("type") == "report_error"]
    assert len(section_events) == 1, f"Expected 1 section despite empty-choices chunks, got: {events}"
    assert len(done_events) == 1
    assert len(error_events) == 0


def test_chunk_with_malformed_choices_does_not_crash():
    """Defensive: even if `choices` is non-list (e.g. dict, string), don't crash."""
    from web import service

    final_json = json.dumps(VALID_SECTIONS_JSON, ensure_ascii=False)
    lines = [
        b'data: {"id":"x","choices":{}}\n\n',
        b'data: {"id":"x","choices":null}\n\n',
        _sse_chunk(final_json),
        b'data: [DONE]\n\n',
    ]

    with patch("web.service.requests.post") as mock_post, \
         patch("web.service.load_provider_settings", return_value=MagicMock(base_url="http://x", api_key="y")), \
         patch("web.service._save_report", new=AsyncMock()):
        mock_post.return_value = _fake_response(lines)
        gen = service.report_stream(user_id=1, data=_make_data(), model_name="test")

        async def _run():
            return await _collect(gen, max_events=20, timeout=10)

        events = asyncio.run(_run())

    section_events = [ev for _, ev in events if ev.get("type") == "report_section"]
    error_events = [ev for _, ev in events if ev.get("type") == "report_error"]
    assert len(section_events) == 1
    assert len(error_events) == 0


# =============================================================================
# Tests for get_report_data matrix filtering by resolved_diagnoses (run-scoped)
# =============================================================================


def _make_run_state(run_id: str, subject_id: str, diagnoses: list, started_at: str, completed_at: str = None):
    return {
        "run_meta": {
            "run_id": run_id,
            "subject_id": subject_id,
            "started_at": started_at,
            "completed_at": completed_at or started_at,
        },
        "diagnoses": diagnoses,
        "steps": [],
        "verification_results": [],
        "normalized_steps": [],
    }


def _recent_timestamp(days_ago: int = 5) -> str:
    """Return an ISO timestamp N days before today (so runs are within 30-day window)."""
    from datetime import datetime, timezone, timedelta
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _make_db_row(run_state: dict) -> dict:
    return {"data": run_state}


def _make_mastery_data(resolved: list[tuple[str, str, str, str]] | None = None) -> dict:
    """resolved: list of (run_id, error_code, step_id, subject_id) tuples."""
    history = []
    for run_id, ec, sid, subj in (resolved or []):
        history.append({
            "run_id": run_id,
            "resolved_diagnoses": [
                {
                    "error_code": ec,
                    "step_id": sid,
                    "subject_id": subj,
                    "resolved_at": "2026-06-01T00:00:00+00:00",
                }
            ],
        })
    return {"errors": {}, "analysis_history": history}


async def _call_get_report_data(runs, mastery):
    """Helper: call get_report_data with mocked database."""
    from web import database
    from web import service

    async def fake_get_all_runs_for_stats(user_id):
        return runs

    async def fake_get_mastery(user_id):
        return mastery

    original = (database.get_all_runs_for_stats, database.get_mastery)
    database.get_all_runs_for_stats = fake_get_all_runs_for_stats
    database.get_mastery = fake_get_mastery
    service.database.get_all_runs_for_stats = fake_get_all_runs_for_stats
    service.database.get_mastery = fake_get_mastery
    try:
        return await service.get_report_data(user_id=1, days=30)
    finally:
        database.get_all_runs_for_stats, database.get_mastery = original
        service.database.get_all_runs_for_stats = original[0]
        service.database.get_mastery = original[1]


def _run_get_report_data(runs, mastery):
    return asyncio.run(_call_get_report_data(runs, mastery))


def _find_cell(matrix_data, skills, subjects, skill, subject):
    try:
        i = skills.index(skill)
        j = subjects.index(subject)
        return matrix_data[i][j]
    except (ValueError, IndexError):
        return None


_CAT_EN_TO_ZH = {
    "Rule Application Errors": "规则应用",
    "Algebraic Manipulation Errors": "代数运算",
    "Theorem/Condition Misuse": "定理/条件",
    "Conceptual Confusion": "概念理解",
    "Reasoning Quality Issues": "逻辑推理",
    "Dimension Errors": "维度分析",
    "Computational Errors": "计算过程",
}


@pytest.mark.asyncio
async def _test_resolved_diagnosis_run_scoped_matrix_async():
    """Marking (E1, S1, math) resolved in run1 must lower error count for
    run1's diagnosis only; run2's same (E1, S1, math) still counts.
    Matrix value for the affected (skill, subject) cell should reflect
    that run1's E1 was filtered out.
    """
    cat_zh = "代数运算"  # maps from "Algebraic Manipulation Errors"
    run1 = _make_run_state("run1", "calculus", [
        {"error_code": "E1", "category": "Algebraic Manipulation Errors", "step_id": "S1", "step_index": 0},
    ], "2026-05-01T00:00:00+00:00")
    run2 = _make_run_state("run2", "calculus", [
        {"error_code": "E1", "category": "Algebraic Manipulation Errors", "step_id": "S1", "step_index": 0},
    ], "2026-05-15T00:00:00+00:00")

    mastery = _make_mastery_data(resolved=[("run1", "E1", "S1", "calculus")])

    report_no_resolve = await _call_get_report_data(
        [_make_db_row(run1), _make_db_row(run2)],
        _make_mastery_data(),
    )
    report_resolve_run1 = await _call_get_report_data(
        [_make_db_row(run1), _make_db_row(run2)],
        mastery,
    )

    skills = report_no_resolve["heatmap_data"]["skills"]
    subjects = report_no_resolve["heatmap_data"]["subjects"]
    val_raw = _find_cell(report_no_resolve["heatmap_data"]["matrix"], skills, subjects, cat_zh, "calculus")
    val_adj = _find_cell(report_resolve_run1["heatmap_data"]["matrix"], skills, subjects, cat_zh, "calculus")
    assert val_raw is not None and val_adj is not None
    assert val_raw == 0.0
    assert val_adj == 0.5


@pytest.mark.asyncio
async def _test_resolved_diagnosis_run_scoped_matrix_async():
    """When the same (E, S, subj) appears in 2 runs, resolving BOTH should
    clear the matrix cell (skill disappears from heatmap because no errors
    remain). Resolving only one run should leave 1 error and the skill
    still appears with low mastery.
    """
    cat_en = "Algebraic Manipulation Errors"
    cat_zh = _CAT_EN_TO_ZH[cat_en]
    run1 = _make_run_state("run1", "calculus", [
        {"error_code": "E1", "category": cat_en, "step_id": "S1", "step_index": 0},
    ], _recent_timestamp(10))
    run2 = _make_run_state("run2", "calculus", [
        {"error_code": "E1", "category": cat_en, "step_id": "S1", "step_index": 0},
    ], _recent_timestamp(3))

    report_no_resolve = await _call_get_report_data(
        [_make_db_row(run1), _make_db_row(run2)],
        _make_mastery_data(),
    )
    report_resolve_both = await _call_get_report_data(
        [_make_db_row(run1), _make_db_row(run2)],
        _make_mastery_data(resolved=[
            ("run1", "E1", "S1", "calculus"),
            ("run2", "E1", "S1", "calculus"),
        ]),
    )
    report_resolve_only_run1 = await _call_get_report_data(
        [_make_db_row(run1), _make_db_row(run2)],
        _make_mastery_data(resolved=[("run1", "E1", "S1", "calculus")]),
    )

    skills_raw = report_no_resolve["heatmap_data"]["skills"]
    skills_both = report_resolve_both["heatmap_data"]["skills"]
    skills_run1 = report_resolve_only_run1["heatmap_data"]["skills"]

    assert cat_zh in skills_raw, f"Raw report should have {cat_zh} in skills; got {skills_raw}"
    assert cat_zh not in skills_both, (
        f"Resolving ALL errors should remove {cat_zh} from skills; got {skills_both}"
    )
    assert cat_zh in skills_run1, (
        f"Resolving only run1 should leave run2's error → {cat_zh} still in skills; got {skills_run1}"
    )


def test_resolved_diagnosis_run_scoped_matrix():
    asyncio.run(_test_resolved_diagnosis_run_scoped_matrix_async())


def _test_resolved_diagnosis_different_run_no_effect_async():
    """When the same (E, S, subj) appears in run1 and run2, marking only
    run1's instance should NOT also clear run2's contribution to the matrix.
    We verify by comparing:
    - Resolve only run1: matrix has 1 unresolved error (from run2)
    - Resolve only run2: matrix has 1 unresolved error (from run1)
    - Resolve both: matrix has 0 unresolved errors
    """
    cat_en = "Algebraic Manipulation Errors"
    cat_zh = _CAT_EN_TO_ZH[cat_en]
    run1 = _make_run_state("run1", "calculus", [
        {"error_code": "E1", "category": cat_en, "step_id": "S1", "step_index": 0},
    ], _recent_timestamp(10))
    run2 = _make_run_state("run2", "calculus", [
        {"error_code": "E1", "category": cat_en, "step_id": "S1", "step_index": 0},
    ], _recent_timestamp(3))

    only_run1 = asyncio.run(_call_get_report_data(
        [_make_db_row(run1), _make_db_row(run2)],
        _make_mastery_data(resolved=[("run1", "E1", "S1", "calculus")]),
    ))
    only_run2 = asyncio.run(_call_get_report_data(
        [_make_db_row(run1), _make_db_row(run2)],
        _make_mastery_data(resolved=[("run2", "E1", "S1", "calculus")]),
    ))

    skills = only_run1["heatmap_data"]["skills"]
    subjects = only_run1["heatmap_data"]["subjects"]
    subj_display = subjects[0]
    val_run1 = _find_cell(only_run1["heatmap_data"]["matrix"], skills, subjects, cat_zh, subj_display)
    val_run2 = _find_cell(only_run2["heatmap_data"]["matrix"], skills, subjects, cat_zh, subj_display)

    assert val_run1 == val_run2, (
        f"Marking only run1 vs only run2 should produce same matrix value "
        f"(both leave 1 error unresolved). Got run1={val_run1}, run2={val_run2}"
    )


def test_resolved_diagnosis_different_run_no_effect():
    _test_resolved_diagnosis_different_run_no_effect_async()


def _test_unresolved_diagnosis_still_in_matrix_async():
    cat_en = "Algebraic Manipulation Errors"
    cat_zh = _CAT_EN_TO_ZH[cat_en]
    run1 = _make_run_state("run1", "calculus", [
        {"error_code": "E1", "category": cat_en, "step_id": "S1", "step_index": 0},
        {"error_code": "E2", "category": cat_en, "step_id": "S2", "step_index": 1},
    ], _recent_timestamp(5))

    report = asyncio.run(_call_get_report_data(
        [_make_db_row(run1)],
        _make_mastery_data(),
    ))

    skills = report["heatmap_data"]["skills"]
    subjects = report["heatmap_data"]["subjects"]
    subj_display = subjects[0]
    val = _find_cell(report["heatmap_data"]["matrix"], skills, subjects, cat_zh, subj_display)
    assert val == 0.0, f"2 errors in 1 run = 0.0 mastery; got {val}"


def test_unresolved_diagnosis_still_in_matrix():
    _test_unresolved_diagnosis_still_in_matrix_async()


def _test_resolved_diagnosis_still_in_radar_async():
    run1 = _make_run_state("run1", "calculus", [
        {"error_code": "E1", "category": "Algebraic Manipulation Errors", "step_id": "S1", "step_index": 0},
    ], _recent_timestamp(5))

    report_raw = asyncio.run(_call_get_report_data(
        [_make_db_row(run1)],
        _make_mastery_data(),
    ))
    report_resolved = asyncio.run(_call_get_report_data(
        [_make_db_row(run1)],
        _make_mastery_data(resolved=[("run1", "E1", "S1", "calculus")]),
    ))

    assert report_raw["radar_data"] == report_resolved["radar_data"]


def test_resolved_diagnosis_still_in_radar():
    _test_resolved_diagnosis_still_in_radar_async()


def _test_resolved_diagnosis_still_in_error_examples_async():
    run1 = _make_run_state("run1", "calculus", [
        {"error_code": "E1", "category": "Algebraic Manipulation Errors", "step_id": "S1", "step_index": 0,
         "root_cause_hypothesis": "test", "supporting_evidence": "test", "confidence": 0.8},
    ], _recent_timestamp(5))

    report_raw = asyncio.run(_call_get_report_data(
        [_make_db_row(run1)],
        _make_mastery_data(),
    ))
    report_resolved = asyncio.run(_call_get_report_data(
        [_make_db_row(run1)],
        _make_mastery_data(resolved=[("run1", "E1", "S1", "calculus")]),
    ))

    assert report_raw["error_examples"] == report_resolved["error_examples"]
    assert "E1" in report_resolved["error_examples"], (
        "Resolved diagnosis should still appear in error_examples (raw history preserved)"
    )


def test_resolved_diagnosis_still_in_error_examples():
    _test_resolved_diagnosis_still_in_error_examples_async()


def _test_resolved_outside_time_window_no_effect_async():
    cat_en = "Algebraic Manipulation Errors"
    cat_zh = _CAT_EN_TO_ZH[cat_en]
    run_recent = _make_run_state("run_recent", "calculus", [
        {"error_code": "E1", "category": cat_en, "step_id": "S1", "step_index": 0},
    ], _recent_timestamp(5))

    report = asyncio.run(_call_get_report_data(
        [_make_db_row(run_recent)],
        _make_mastery_data(resolved=[("run_old_outside_window", "E1", "S1", "calculus")]),
    ))

    skills = report["heatmap_data"]["skills"]
    subjects = report["heatmap_data"]["subjects"]
    subj_display = subjects[0]
    val = _find_cell(report["heatmap_data"]["matrix"], skills, subjects, cat_zh, subj_display)
    assert val == 0.0, f"Resolved marker for run not in window should not affect matrix; got {val}"


def test_resolved_outside_time_window_no_effect():
    _test_resolved_outside_time_window_no_effect_async()
