from __future__ import annotations

import re
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from fastapi import Body, Depends, FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from stem_tutor.settings import load_provider_settings
from stem_tutor.subjects.context import get_subject_context
from stem_tutor.subjects.detector import detect_subject
from stem_tutor.subjects.loader import SubjectRegistry
from stem_tutor.taxonomy.errors import lookup_error
from web.auth import create_access_token, get_admin_user, get_current_user, hash_password, verify_password
from web.database import _ensure_db, create_user, get_user_by_username
from web.models import LoginRequest, RegisterRequest
from web.service import ocr_problem_text, run_stem_tutor, run_stem_tutor_stream, _load_run_result, _get_run_status
from web.service import _load_chat_history, chat_stream, list_runs, get_stats, reverify_step, cancel_run
from web.service import delete_runs, cleanup_runs_before
from web.service import get_report_data, report_stream, get_report_run_list
from web.service import list_reports, _load_report, delete_reports
from web.service import practice_verify_stream
from web.service import practice_reference_stream

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="STEM Tutor")


@app.on_event("startup")
async def startup():
    await _ensure_db()
    admin = await get_user_by_username("admin")
    if not admin:
        pw_hash = hash_password("admin123")
        await create_user("admin", pw_hash, is_admin=True)


@app.post("/api/auth/register")
async def register(req: RegisterRequest):
    username = req.username.strip()
    password = req.password
    if len(username) < 2 or len(username) > 32:
        return JSONResponse(status_code=400, content={"detail": "\u7528\u6237\u540d\u9700\u89812-32\u4e2a\u5b57\u7b26"})
    if len(password) < 4:
        return JSONResponse(status_code=400, content={"detail": "\u5bc6\u7801\u81f3\u5c114\u4f4d"})
    existing = await get_user_by_username(username)
    if existing:
        return JSONResponse(status_code=409, content={"detail": "\u7528\u6237\u540d\u5df2\u5b58\u5728"})
    pw_hash = hash_password(password)
    user_id = await create_user(username, pw_hash)
    token = create_access_token(user_id, username)
    return {"access_token": token, "token_type": "bearer", "user": {"id": user_id, "username": username, "is_admin": False}}


@app.post("/api/auth/login")
async def login(req: LoginRequest):
    user = await get_user_by_username(req.username.strip())
    if not user or not verify_password(req.password, user["password_hash"]):
        return JSONResponse(status_code=401, content={"detail": "\u7528\u6237\u540d\u6216\u5bc6\u7801\u9519\u8bef"})
    token = create_access_token(user["id"], user["username"], bool(user["is_admin"]))
    return {"access_token": token, "token_type": "bearer", "user": {"id": user["id"], "username": user["username"], "is_admin": bool(user["is_admin"])}}


@app.get("/api/auth/me")
async def me(user: dict = Depends(get_current_user)):
    return {"id": user["id"], "username": user["username"], "is_admin": bool(user["is_admin"])}


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
    user: dict = Depends(get_current_user),
):
    uid = user["id"]
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
        result = await run_stem_tutor(
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
            user_id=uid,
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
    user: dict = Depends(get_current_user),
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
            user_id=user["id"],
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
async def analyze_status(run_id: str, user: dict = Depends(get_current_user)):
    status = await _get_run_status(run_id, user["id"])
    if status["status"] == "not_found":
        return JSONResponse(status_code=404, content=status)
    return JSONResponse(content=status)


@app.get("/analyze/result/{run_id}")
async def analyze_result(run_id: str, user: dict = Depends(get_current_user)):
    result = await _load_run_result(run_id, user["id"])
    if result is None:
        return JSONResponse(status_code=404, content={"error": "结果不存在", "run_id": run_id})
    return JSONResponse(content=result)


@app.get("/chat/history/{run_id}")
async def chat_history(run_id: str, user: dict = Depends(get_current_user)):
    history = await _load_chat_history(run_id, user["id"])
    return JSONResponse(content={"run_id": run_id, "messages": history})


@app.post("/chat/stream")
async def chat_stream_endpoint(
    run_id: str = Form(...),
    message: str = Form(...),
    model: str = Form("DeepSeek-V3.2"),
    user: dict = Depends(get_current_user),
):
    result = await _load_run_result(run_id, user["id"])
    if result is None:
        return JSONResponse(status_code=404, content={"error": "分析结果不存在"})

    async def event_generator():
        async for chunk in chat_stream(run_id, message, model_name=model, user_id=user["id"]):
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
    user: dict = Depends(get_current_user),
):
    return JSONResponse(content=await list_runs(user["id"], subject=subject, status=status, search=search, page=page, per_page=per_page))


@app.get("/stats")
async def stats(user: dict = Depends(get_current_user)):
    return JSONResponse(content=await get_stats(user["id"]))


@app.delete("/api/runs")
async def delete_runs_endpoint(run_ids: list[str] = Body(..., embed=True), user: dict = Depends(get_current_user)):
    result = await delete_runs(user["id"], run_ids)
    return JSONResponse(content=result)


@app.post("/analyze/cancel/{run_id}")
async def cancel_run_endpoint(run_id: str, user: dict = Depends(get_current_user)):
    ok = await cancel_run(run_id, user["id"])
    return JSONResponse(content={"cancelled": ok})


@app.post("/api/runs/cleanup")
async def cleanup_runs_endpoint(before_days: int = 30, user: dict = Depends(get_current_user)):
    result = await cleanup_runs_before(user["id"], before_days)
    return JSONResponse(content=result)


@app.post("/api/verify-step")
async def reverify_step_endpoint(
    run_id: str = Form(...),
    step_id: str = Form(...),
    user: dict = Depends(get_current_user),
):
    try:
        result = await reverify_step(run_id, step_id, user_id=user["id"])
        return JSONResponse(content=result)
    except Exception as exc:
        return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})


@app.post("/practice/verify")
async def practice_verify_endpoint(
    problem_text: str = Form(""),
    student_solution: str = Form(""),
    subject_id: str = Form("calculus"),
    related_weakness_code: str = Form(""),
):
    if not problem_text.strip():
        return JSONResponse(status_code=400, content={"error": "请输入题目"})
    if not student_solution.strip():
        return JSONResponse(status_code=400, content={"error": "请输入解题步骤"})

    resolved_subject_id = subject_id
    if resolved_subject_id == "auto_detect":
        try:
            settings = load_provider_settings()
            resolved_subject_id = detect_subject(
                problem_text.strip(),
                base_url=settings.base_url,
                api_key=settings.api_key,
                model=settings.detection_model_name,
            )
        except Exception:
            resolved_subject_id = "calculus"

    async def event_generator():
        async for chunk in practice_verify_stream(
            problem_text=problem_text.strip(),
            student_solution=student_solution.strip(),
            subject_id=resolved_subject_id,
            related_weakness_code=related_weakness_code,
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


@app.post("/practice/reference")
async def practice_reference_endpoint(
    problem_text: str = Form(""),
    subject_id: str = Form("calculus"),
):
    if not problem_text.strip():
        return JSONResponse(status_code=400, content={"error": "请输入题目"})

    async def event_generator():
        async for chunk in practice_reference_stream(
            problem_text=problem_text.strip(),
            subject_id=subject_id,
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


@app.get("/report/runs")
async def report_runs_endpoint(user: dict = Depends(get_current_user)):
    return JSONResponse(content=await get_report_run_list(user["id"]))


@app.get("/report/data")
async def report_data_endpoint(
    days: int = 30,
    start_date: str | None = None,
    end_date: str | None = None,
    run_ids: str | None = None,
    user: dict = Depends(get_current_user),
):
    ids = run_ids.split(",") if run_ids else None
    return JSONResponse(content=await get_report_data(user["id"], days, start_date=start_date, end_date=end_date, run_ids=ids))


@app.post("/report/generate")
async def report_generate_endpoint(
    days: int = Form(30),
    model: str = Form("qwen/qwen3.6-plus"),
    start_date: str = Form(""),
    end_date: str = Form(""),
    run_ids: str = Form(""),
    user: dict = Depends(get_current_user),
):
    import logging
    logger = logging.getLogger(__name__)

    ids = run_ids.split(",") if run_ids else None
    sd = start_date or None
    ed = end_date or None
    logger.info("[Report] Generate request: model=%s, days=%s, start=%s, end=%s, run_ids=%s", model, days, sd, ed, ids)

    data = await get_report_data(user["id"], days, start_date=sd, end_date=ed, run_ids=ids)
    if data["total_runs"] < 1:
        logger.warning("[Report] No runs found for the given filter")
        return JSONResponse(status_code=400, content={"error": "暂无诊断记录，无法生成报告"})

    logger.info("[Report] Data collected: total_runs=%d", data["total_runs"])

    async def event_generator():
        try:
            async for chunk in report_stream(data, model_name=model, user_id=user["id"]):
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
async def report_list_endpoint(page: int = 1, per_page: int = 20, user: dict = Depends(get_current_user)):
    return JSONResponse(content=await list_reports(user["id"], page, per_page))


@app.get("/report/{report_id}")
async def report_detail_endpoint(report_id: str, user: dict = Depends(get_current_user)):
    report = await _load_report(report_id, user["id"])
    if not report:
        return JSONResponse(status_code=404, content={"error": "报告不存在"})
    return JSONResponse(content=report)


@app.delete("/report/{report_id}")
async def report_delete_endpoint(report_id: str, user: dict = Depends(get_current_user)):
    result = await delete_reports(user["id"], [report_id])
    if result["deleted"] > 0:
        return JSONResponse(content={"ok": True})
    return JSONResponse(status_code=404, content={"error": "报告不存在"})


@app.get("/api/user/settings")
async def get_user_settings(user: dict = Depends(get_current_user)):
    from web.database import get_settings as db_get_settings
    return await db_get_settings(user["id"])


@app.post("/api/user/settings")
async def save_user_settings(data: dict = Body(...), user: dict = Depends(get_current_user)):
    from web.database import save_settings as db_save_settings
    await db_save_settings(user["id"], data)
    return {"ok": True}


@app.get("/api/user/mastery")
async def get_user_mastery(user: dict = Depends(get_current_user)):
    from web.database import get_mastery as db_get_mastery
    return await db_get_mastery(user["id"])


@app.post("/api/user/mastery")
async def save_user_mastery(data: dict = Body(...), user: dict = Depends(get_current_user)):
    from web.database import save_mastery as db_save_mastery
    await db_save_mastery(user["id"], data)
    return {"ok": True}


@app.get("/api/admin/users")
async def admin_users(admin: dict = Depends(get_admin_user)):
    from web.database import get_db
    db = await get_db()
    try:
        cur = await db.execute("SELECT id, username, is_admin, created_at FROM users ORDER BY id")
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


@app.get("/api/admin/stats")
async def admin_stats(admin: dict = Depends(get_admin_user)):
    from web.database import get_db
    db = await get_db()
    try:
        cur = await db.execute("SELECT COUNT(*) FROM users")
        user_count = (await cur.fetchone())[0]
        cur = await db.execute("SELECT COUNT(*) FROM runs")
        run_count = (await cur.fetchone())[0]
        return {"user_count": user_count, "run_count": run_count}
    finally:
        await db.close()


@app.delete("/api/admin/users/{user_id}")
async def admin_delete_user(user_id: int, admin: dict = Depends(get_admin_user)):
    from web.database import get_db
    db = await get_db()
    try:
        await db.execute("DELETE FROM runs WHERE user_id=?", (user_id,))
        await db.execute("DELETE FROM chats WHERE user_id=?", (user_id,))
        await db.execute("DELETE FROM reports WHERE user_id=?", (user_id,))
        await db.execute("DELETE FROM user_settings WHERE user_id=?", (user_id,))
        await db.execute("DELETE FROM user_mastery WHERE user_id=?", (user_id,))
        await db.execute("DELETE FROM users WHERE id=?", (user_id,))
        await db.commit()
        return {"ok": True}
    finally:
        await db.close()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("web.app:app", reload=True)
