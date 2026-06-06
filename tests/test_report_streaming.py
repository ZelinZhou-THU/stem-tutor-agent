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
