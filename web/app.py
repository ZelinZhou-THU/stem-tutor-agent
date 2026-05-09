from __future__ import annotations

import re
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from fastapi import Body, FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from stem_tutor.settings import load_provider_settings
from stem_tutor.subjects.context import get_subject_context
from stem_tutor.subjects.detector import detect_subject
from stem_tutor.subjects.loader import SubjectRegistry
from stem_tutor.taxonomy.errors import lookup_error
from web.service import ocr_problem_text, run_stem_tutor, run_stem_tutor_stream, _load_run_result, _get_run_status
from web.service import _load_chat_history, chat_stream, list_runs, get_stats, reverify_step
from web.service import delete_runs, cleanup_runs_before
from web.service import get_report_data, report_stream, get_report_run_list
from web.service import list_reports, _load_report, delete_reports

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="STEM Tutor")


class NoCacheStaticMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
            if "etag" in response.headers:
                del response.headers["etag"]
        return response


app.add_middleware(NoCacheStaticMiddleware)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

INDEX_HTML = BASE_DIR / "templates" / "index.html"


def _extract_ocr_model_name(warnings: list[str]) -> str | None:
    for w in warnings:
        m = re.match(r"ocr_model=(.+)", w)
        if m:
            return m.group(1).strip()
    return None


@app.get("/", response_class=HTMLResponse)
async def index():
    return INDEX_HTML.read_text(encoding="utf-8")


@app.post("/ocr")
async def ocr_endpoint(
    image: UploadFile = File(...),
    model: str = Form("qwen/qwen3.6-plus"),
):
    img_bytes = await image.read()
    try:
        result = ocr_problem_text(img_bytes, provider_name="openai-compatible")
        return JSONResponse(content=result)
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": f"OCR 失败：{exc}"})


@app.post("/analyze")
async def analyze(
    problem_text: str = Form(""),
    source_type: str = Form("text"),
    student_solution: str = Form(""),
    model: str = Form("qwen/qwen3.6-plus"),
    subject_id: str = Form("calculus"),
    mode: str = Form("workflow_r1"),
    depth: str = Form("standard"),
    image: UploadFile | None = File(None),
    problem_image: UploadFile | None = File(None),
):
    resolved_problem_text = problem_text.strip()
    ocr_model_name = None

    if problem_image is not None:
        try:
            img_bytes = await problem_image.read()
            ocr_result = ocr_problem_text(img_bytes, provider_name="openai-compatible")
            resolved_problem_text = ocr_result["text"] or resolved_problem_text
            ocr_model_name = _extract_ocr_model_name(ocr_result.get("warnings", []))
        except Exception:
            pass

    if not resolved_problem_text:
        return JSONResponse(status_code=400, content={"error": "请输入题目或上传题目照片"})

    if source_type == "text" and not student_solution.strip():
        return JSONResponse(status_code=400, content={"error": "请输入学生的解题步骤"})

    if source_type == "ocr" and image is None:
        return JSONResponse(status_code=400, content={"error": "请上传解题照片"})

    image_bytes: bytes | None = None
    if image is not None:
        image_bytes = await image.read()

    try:
        result = run_stem_tutor(
            problem_text=resolved_problem_text,
            raw_student_solution=student_solution,
            source_type=source_type,
            image_bytes=image_bytes,
            provider_name="openai-compatible",
            model_name=model,
            ocr_model_name=ocr_model_name,
            subject_id=subject_id,
            mode=mode,
            depth=depth,
        )
        return JSONResponse(content=result)
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": f"分析失败：{exc}"})


@app.post("/analyze/stream")
async def analyze_stream(
    problem_text: str = Form(""),
    source_type: str = Form("text"),
    student_solution: str = Form(""),
    model: str = Form("qwen/qwen3.6-plus"),
    subject_id: str = Form("calculus"),
    mode: str = Form("workflow_r1"),
    depth: str = Form("standard"),
    image: UploadFile | None = File(None),
    problem_image: UploadFile | None = File(None),
):
    resolved_problem_text = problem_text.strip()
    ocr_model_name = None

    if problem_image is not None:
        try:
            img_bytes = await problem_image.read()
            ocr_result = ocr_problem_text(img_bytes, provider_name="openai-compatible")
            resolved_problem_text = ocr_result["text"] or resolved_problem_text
            ocr_model_name = _extract_ocr_model_name(ocr_result.get("warnings", []))
        except Exception:
            pass

    if not resolved_problem_text:
        return JSONResponse(status_code=400, content={"error": "请输入题目或上传题目照片"})

    if source_type == "text" and not student_solution.strip():
        return JSONResponse(status_code=400, content={"error": "请输入学生的解题步骤"})

    if source_type == "ocr" and image is None:
        return JSONResponse(status_code=400, content={"error": "请上传解题照片"})

    image_bytes: bytes | None = None
    if image is not None:
        image_bytes = await image.read()

    async def event_generator():
        async for chunk in run_stem_tutor_stream(
            problem_text=resolved_problem_text,
            raw_student_solution=student_solution,
            source_type=source_type,
            image_bytes=image_bytes,
            provider_name="openai-compatible",
            model_name=model,
            ocr_model_name=ocr_model_name,
            subject_id=subject_id,
            mode=mode,
            depth=depth,
        ):
            yield chunk

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/detect-subject")
async def detect_subject_endpoint(
    problem_text: str = Form(""),
):
    if not problem_text.strip():
        return JSONResponse(status_code=400, content={"error": "请输入题目"})
    settings = load_provider_settings()
    subject_id = detect_subject(
        problem_text.strip(),
        base_url=settings.base_url,
        api_key=settings.api_key,
        model=settings.detection_model_name,
    )
    try:
        ctx = get_subject_context(subject_id)
        display_name = ctx.display_name
    except Exception:
        display_name = subject_id
    return JSONResponse(content={"subject_id": subject_id, "display_name": display_name})


@app.get("/analyze/status/{run_id}")
async def analyze_status(run_id: str):
    status = _get_run_status(run_id)
    if status["status"] == "not_found":
        return JSONResponse(status_code=404, content=status)
    return JSONResponse(content=status)


@app.get("/analyze/result/{run_id}")
async def analyze_result(run_id: str):
    result = _load_run_result(run_id)
    if result is None:
        return JSONResponse(status_code=404, content={"error": "结果不存在", "run_id": run_id})
    return JSONResponse(content=result)


@app.get("/chat/history/{run_id}")
async def chat_history(run_id: str):
    history = _load_chat_history(run_id)
    return JSONResponse(content={"run_id": run_id, "messages": history})


@app.post("/chat/stream")
async def chat_stream_endpoint(
    run_id: str = Form(...),
    message: str = Form(...),
    model: str = Form("DeepSeek-V3.2"),
):
    result = _load_run_result(run_id)
    if result is None:
        return JSONResponse(status_code=404, content={"error": "分析结果不存在"})

    async def event_generator():
        async for chunk in chat_stream(run_id, message, model_name=model):
            yield chunk

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/history")
async def history(
    subject: str | None = None,
    status: str | None = None,
    search: str | None = None,
    page: int = 1,
    per_page: int = 20,
):
    return JSONResponse(content=list_runs(subject=subject, status=status, search=search, page=page, per_page=per_page))


@app.get("/stats")
async def stats():
    return JSONResponse(content=get_stats())


@app.delete("/api/runs")
async def delete_runs_endpoint(run_ids: list[str] = Body(..., embed=True)):
    result = delete_runs(run_ids)
    return JSONResponse(content=result)


@app.post("/api/runs/cleanup")
async def cleanup_runs_endpoint(before_days: int = 30):
    result = cleanup_runs_before(before_days)
    return JSONResponse(content=result)


@app.post("/api/verify-step")
async def reverify_step_endpoint(
    run_id: str = Form(...),
    step_id: str = Form(...),
):
    try:
        result = reverify_step(run_id, step_id)
        return JSONResponse(content=result)
    except Exception as exc:
        return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})


@app.get("/report/runs")
async def report_runs_endpoint():
    return JSONResponse(content=get_report_run_list())


@app.get("/report/data")
async def report_data_endpoint(
    days: int = 30,
    start_date: str | None = None,
    end_date: str | None = None,
    run_ids: str | None = None,
):
    ids = run_ids.split(",") if run_ids else None
    return JSONResponse(content=get_report_data(days, start_date=start_date, end_date=end_date, run_ids=ids))


@app.post("/report/generate")
async def report_generate_endpoint(
    days: int = Form(30),
    model: str = Form("qwen/qwen3.6-plus"),
    start_date: str = Form(""),
    end_date: str = Form(""),
    run_ids: str = Form(""),
):
    import logging
    logger = logging.getLogger(__name__)

    ids = run_ids.split(",") if run_ids else None
    sd = start_date or None
    ed = end_date or None
    logger.info("[Report] Generate request: model=%s, days=%s, start=%s, end=%s, run_ids=%s", model, days, sd, ed, ids)

    data = get_report_data(days, start_date=sd, end_date=ed, run_ids=ids)
    if data["total_runs"] < 1:
        logger.warning("[Report] No runs found for the given filter")
        return JSONResponse(status_code=400, content={"error": "暂无诊断记录，无法生成报告"})

    logger.info("[Report] Data collected: total_runs=%d", data["total_runs"])

    async def event_generator():
        try:
            async for chunk in report_stream(data, model_name=model):
                yield chunk
        except Exception as exc:
            logger.error("[Report] event_generator error: %s", exc, exc_info=True)
            import json as _json
            yield f"data: {_json.dumps({'type': 'report_error', 'message': f'流式传输失败：{exc}'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/report/list")
async def report_list_endpoint(page: int = 1, per_page: int = 20):
    return JSONResponse(content=list_reports(page, per_page))


@app.get("/report/{report_id}")
async def report_detail_endpoint(report_id: str):
    report = _load_report(report_id)
    if not report:
        return JSONResponse(status_code=404, content={"error": "报告不存在"})
    return JSONResponse(content=report)


@app.delete("/report/{report_id}")
async def report_delete_endpoint(report_id: str):
    result = delete_reports([report_id])
    if result["deleted"] > 0:
        return JSONResponse(content={"ok": True})
    return JSONResponse(status_code=404, content={"error": "报告不存在"})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("web.app:app", reload=True)
