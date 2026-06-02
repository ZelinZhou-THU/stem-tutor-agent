from __future__ import annotations

import json
import logging
import os
import re
import secrets
import time
from collections import defaultdict
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from fastapi import Body, Depends, FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from stem_tutor.settings import load_provider_settings
from stem_tutor.subjects.context import get_subject_context
from stem_tutor.subjects.detector import detect_subject
from stem_tutor.subjects.loader import SubjectRegistry
from stem_tutor.taxonomy.errors import lookup_error
from web.auth import create_access_token, get_admin_user, get_current_user, get_current_user_allow_restricted, hash_password, verify_password
from web.batch_worker import get_batch_worker
from web.database import (
    _ensure_db, create_user, get_user_by_username, get_user_for_login,
    create_batch, load_batch, update_batch_status, update_batch_item,
    add_batch_items, list_batch_items, list_batches, delete_batch,
    close_pool,
)
from web.models import LoginRequest, RegisterRequest, ChangePasswordRequest
from web.service import ocr_problem_text, run_stem_tutor, run_stem_tutor_stream, _load_run_result, _get_run_status
from web.service import _load_chat_history, chat_stream, list_runs, get_stats, reverify_step, cancel_run
from web.service import delete_runs, cleanup_runs_before
from web.service import get_report_data, report_stream, get_report_run_list
from web.service import list_reports, _load_report, delete_reports
from web.service import practice_verify_stream
from web.service import practice_reference_stream

BASE_DIR = Path(__file__).resolve().parent

MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10MB
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

app = FastAPI(title="STEM Tutor", docs_url=None, redoc_url=None, openapi_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://www.zelin.online", "http://localhost:8000", "http://127.0.0.1:8000"],
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
    allow_credentials=True,
)


@app.on_event("startup")
async def startup():
    await _ensure_db()
    admin = await get_user_by_username("admin")
    if not admin:
        pw = secrets.token_urlsafe(16)
        pw_hash = hash_password(pw)
        await create_user("admin", pw_hash, is_admin=True, force_change_password=True)
        pw_file = Path(__file__).resolve().parent.parent / "data" / "admin_password.txt"
        pw_file.parent.mkdir(parents=True, exist_ok=True)
        pw_file.write_text(pw, encoding="utf-8")
        logging.getLogger("uvicorn").info(
            "══════════════════════════════════════════════════\n"
            "  管理员初始密码已生成，已写入密码文件\n"
            "  请登录后立即修改密码，密码文件: %s\n"
            "══════════════════════════════════════════════════", pw_file
        )
    await get_batch_worker().start()


@app.on_event("shutdown")
async def shutdown():
    await get_batch_worker().stop()
    await close_pool()


@app.post("/api/auth/register")
async def register(req: RegisterRequest):
    username = req.username.strip()
    password = req.password
    if len(username) < 2 or len(username) > 32:
        return JSONResponse(status_code=400, content={"detail": "\u7528\u6237\u540d\u9700\u89812-32\u4e2a\u5b57\u7b26"})
    if len(password) < 8:
        return JSONResponse(status_code=400, content={"detail": "\u5bc6\u7801\u81f3\u5c118\u4f4d"})
    existing = await get_user_by_username(username)
    if existing:
        if existing.get("status") == "pending":
            return JSONResponse(status_code=409, content={"detail": "该用户名已提交注册申请，请等待审批"})
        return JSONResponse(status_code=409, content={"detail": "用户名已存在"})
    pw_hash = hash_password(password)
    user_id = await create_user(username, pw_hash, status="pending")
    return {"message": "注册成功，请等待管理员审批后登录", "status": "pending"}


@app.post("/api/auth/login")
async def login(req: LoginRequest):
    user = await get_user_for_login(req.username.strip())
    if not user or not verify_password(req.password, user["password_hash"]):
        return JSONResponse(status_code=401, content={"detail": "\u7528\u6237\u540d\u6216\u5bc6\u7801\u9519\u8bef"})
    if user.get("status") == "pending":
        return JSONResponse(status_code=403, content={"detail": "账号正在等待管理员审批"})
    force_change = bool(user.get("force_change_password"))
    token = create_access_token(user["id"], user["username"], bool(user["is_admin"]), restricted=force_change)
    return {"access_token": token, "token_type": "bearer", "user": {"id": user["id"], "username": user["username"], "is_admin": bool(user["is_admin"]), "force_change_password": force_change}}


@app.get("/api/auth/me")
async def me(user: dict = Depends(get_current_user_allow_restricted)):
    return {"id": user["id"], "username": user["username"], "is_admin": bool(user["is_admin"]), "force_change_password": bool(user.get("force_change_password"))}


@app.post("/api/user/change-password")
async def change_password(req: ChangePasswordRequest, user: dict = Depends(get_current_user_allow_restricted)):
    if len(req.new_password) < 8:
        return JSONResponse(status_code=400, content={"detail": "新密码至少8位"})
    from web.database import get_user_password_hash, update_password
    pw_hash = await get_user_password_hash(user["id"])
    if not pw_hash or not verify_password(req.old_password, pw_hash):
        return JSONResponse(status_code=400, content={"detail": "当前密码错误"})
    if req.old_password == req.new_password:
        return JSONResponse(status_code=400, content={"detail": "新密码不能与当前密码相同"})
    new_hash = hash_password(req.new_password)
    await update_password(user["id"], new_hash)
    pw_file = Path(__file__).resolve().parent.parent / "data" / "admin_password.txt"
    if pw_file.exists():
        try:
            pw_file.unlink()
        except OSError:
            pass
    token = create_access_token(user["id"], user["username"], bool(user["is_admin"]))
    return {"ok": True, "access_token": token, "token_type": "bearer"}


@app.post("/api/auth/logout")
async def logout(user: dict = Depends(get_current_user)):
    return {"ok": True, "message": "已退出登录"}


_RATE_LIMIT_WINDOW = 60
_RATE_LIMIT_MAX = 30
_rate_requests: dict[str, list[float]] = defaultdict(list)


def _get_client_ip(request: Request) -> str:
    host = request.client.host if request.client else "unknown"
    if host in ("127.0.0.1", "::1"):
        forwarded = request.headers.get("CF-Connecting-IP") or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        if forwarded:
            return forwarded
    return host


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        now = time.time()
        client_ip = _get_client_ip(request)
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            timestamps = _rate_requests[client_ip]
            _rate_requests[client_ip] = [t for t in timestamps if now - t < _RATE_LIMIT_WINDOW]
            _rate_requests[client_ip].append(now)
            if len(_rate_requests[client_ip]) > _RATE_LIMIT_MAX:
                return JSONResponse(status_code=429, content={"detail": "请求过于频繁，请稍后重试"})
        response = await call_next(request)
        return response


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


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response


app.add_middleware(RateLimitMiddleware)
app.add_middleware(NoCacheStaticMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

INDEX_HTML = BASE_DIR / "templates" / "index.html"


def _extract_ocr_model_name(warnings: list[str]) -> str | None:
    for w in warnings:
        m = re.match(r"ocr_model=(.+)", w)
        if m:
            return m.group(1).strip()
    return None


def _is_allowed_upload(image: UploadFile | None) -> bool:
    if image is None:
        return False
    content_type = (image.content_type or "").strip().lower()
    return content_type in ALLOWED_IMAGE_TYPES


def _sniff_upload_mime(image_bytes: bytes) -> str | None:
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image_bytes.startswith(b"GIF87a") or image_bytes.startswith(b"GIF89a"):
        return "image/gif"
    if image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    if image_bytes.startswith(b"\xff\xd8"):
        return "image/jpeg"
    return None


def _validate_upload_bytes(image: UploadFile, image_bytes: bytes) -> bool:
    sniffed = _sniff_upload_mime(image_bytes)
    if sniffed is None:
        return False
    declared = (image.content_type or "").strip().lower()
    if declared and declared not in ALLOWED_IMAGE_TYPES:
        return False
    if declared and declared != sniffed:
        return False
    return sniffed in ALLOWED_IMAGE_TYPES


@app.get("/", response_class=HTMLResponse)
async def index():
    return INDEX_HTML.read_text(encoding="utf-8")


@app.post("/ocr")
async def ocr_endpoint(
    image: UploadFile = File(...),
    model: str = Form("qwen/qwen3.6-plus"),
    user: dict = Depends(get_current_user),
):
    img_bytes = await image.read(MAX_UPLOAD_SIZE + 1)
    if len(img_bytes) > MAX_UPLOAD_SIZE:
        return JSONResponse(status_code=413, content={"error": "文件大小超过 10MB 限制"})
    if not _validate_upload_bytes(image, img_bytes):
        return JSONResponse(status_code=400, content={"error": "仅支持 JPEG/PNG/WebP/GIF 格式图片"})
    try:
        result = ocr_problem_text(img_bytes, provider_name="openai-compatible")
        return JSONResponse(content=result)
    except Exception as exc:
        logging.getLogger("stem_tutor.app").exception("OCR failed")
        return JSONResponse(status_code=500, content={"error": "OCR 处理失败，请稍后重试"})


@app.post("/analyze")
async def analyze(
    problem_text: str = Form(""),
    source_type: str = Form("text"),
    student_solution: str = Form(""),
    model: str = Form("qwen/qwen3.6-plus"),
    subject_id: str = Form("calculus"),
    mode: str = Form("workflow_r1"),
    depth: str = Form("with_ref"),
    image: UploadFile | None = File(None),
    problem_image: UploadFile | None = File(None),
    user: dict = Depends(get_current_user),
):
    uid = user["id"]
    resolved_problem_text = problem_text.strip()
    ocr_model_name = None

    if problem_image is not None:
        try:
            img_bytes = await problem_image.read(MAX_UPLOAD_SIZE + 1)
            if len(img_bytes) > MAX_UPLOAD_SIZE:
                return JSONResponse(status_code=413, content={"error": "图片大小超过 10MB 限制"})
            if not _validate_upload_bytes(problem_image, img_bytes):
                return JSONResponse(status_code=400, content={"error": "仅支持 JPEG/PNG/WebP/GIF 格式图片"})
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
        image_bytes = await image.read(MAX_UPLOAD_SIZE + 1)
        if len(image_bytes) > MAX_UPLOAD_SIZE:
            return JSONResponse(status_code=413, content={"error": "图片大小超过 10MB 限制"})
        if not _validate_upload_bytes(image, image_bytes):
            return JSONResponse(status_code=400, content={"error": "仅支持 JPEG/PNG/WebP/GIF 格式图片"})

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
            user_id=uid,
        )
        return JSONResponse(content=result)
    except Exception as exc:
        logging.getLogger("stem_tutor.app").exception("Analyze failed")
        return JSONResponse(status_code=500, content={"error": "分析失败，请稍后重试"})


@app.post("/analyze/stream")
async def analyze_stream(
    problem_text: str = Form(""),
    source_type: str = Form("text"),
    student_solution: str = Form(""),
    model: str = Form("qwen/qwen3.6-plus"),
    subject_id: str = Form("calculus"),
    mode: str = Form("workflow_r1"),
    depth: str = Form("with_ref"),
    image: UploadFile | None = File(None),
    problem_image: UploadFile | None = File(None),
    user: dict = Depends(get_current_user),
):
    resolved_problem_text = problem_text.strip()
    ocr_model_name = None

    if problem_image is not None:
        try:
            img_bytes = await problem_image.read(MAX_UPLOAD_SIZE + 1)
            if len(img_bytes) > MAX_UPLOAD_SIZE:
                return JSONResponse(status_code=413, content={"error": "图片大小超过 10MB 限制"})
            if not _validate_upload_bytes(problem_image, img_bytes):
                return JSONResponse(status_code=400, content={"error": "仅支持 JPEG/PNG/WebP/GIF 格式图片"})
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
        image_bytes = await image.read(MAX_UPLOAD_SIZE + 1)
        if len(image_bytes) > MAX_UPLOAD_SIZE:
            return JSONResponse(status_code=413, content={"error": "图片大小超过 10MB 限制"})
        if not _validate_upload_bytes(image, image_bytes):
            return JSONResponse(status_code=400, content={"error": "仅支持 JPEG/PNG/WebP/GIF 格式图片"})

    settings = {
        "model": model,
        "subject_id": subject_id,
        "mode": mode,
        "depth": depth,
        "student_solution": student_solution,
        "source_type": source_type,
        "image_stored": image_bytes is not None,
    }
    batch_id = await create_batch(user["id"], settings, total_count=1)
    await add_batch_items(batch_id, [{
        "problem_text": resolved_problem_text,
        "student_solution": student_solution,
        "source_type": source_type,
    }])
    await update_batch_status(batch_id, "running")
    get_batch_worker().notify()

    async def _quick_stream():
        yield f"data: {json.dumps({'type': 'start', 'batch_id': batch_id}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type': 'done', 'message': '任务已提交，正在后台处理...'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        _quick_stream(),
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
    user: dict = Depends(get_current_user),
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


@app.get("/analyze/batch-status/{batch_id}")
async def batch_analyze_status(batch_id: str, user: dict = Depends(get_current_user)):
    batch = await load_batch(batch_id, user["id"])
    if not batch:
        return JSONResponse(status_code=404, content={"error": "批处理不存在"})
    items = await list_batch_items(batch_id)
    item = items[0] if items else None
    if item and item.get("run_id"):
        run_status = await _get_run_status(item["run_id"], user["id"])
        return JSONResponse(content={"status": run_status.get("status", "running"), "run_id": item["run_id"], **run_status})
    if item and item.get("status") == "running":
        return JSONResponse(content={"status": "running", "run_id": None})
    if item and item.get("status") == "failed":
        return JSONResponse(content={"status": "failed", "error": item.get("error_message", "分析失败")})
    return JSONResponse(content={"status": "pending"})


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
        async for chunk in chat_stream(run_id, user_id=user["id"], user_message=message, model_name=model):
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
        result = await reverify_step(run_id, user_id=user["id"], step_id=step_id)
        return JSONResponse(content=result)
    except Exception as exc:
        logging.getLogger("stem_tutor.app").exception("Reverify step failed")
        return JSONResponse(status_code=500, content={"success": False, "error": "验证失败，请稍后重试"})


@app.post("/practice/verify")
async def practice_verify_endpoint(
    problem_text: str = Form(""),
    student_solution: str = Form(""),
    subject_id: str = Form("calculus"),
    related_weakness_code: str = Form(""),
    user: dict = Depends(get_current_user),
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
    user: dict = Depends(get_current_user),
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
            async for chunk in report_stream(user_id=user["id"], data=data, model_name=model):
                yield chunk
        except Exception as exc:
            logger.error("[Report] event_generator error: %s", exc, exc_info=True)
            import json as _json
            yield f"data: {_json.dumps({'type': 'report_error', 'message': '流式传输失败，请稍后重试'}, ensure_ascii=False)}\n\n"

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


@app.get("/api/admin/users/{user_id}")
async def admin_get_user_detail(user_id: int, admin: dict = Depends(get_admin_user)):
    from web.database import get_user_by_id, get_settings, get_mastery
    user = await get_user_by_id(user_id)
    if not user:
        return JSONResponse(status_code=404, content={"error": "用户不存在"})
    settings = await get_settings(user_id)
    mastery = await get_mastery(user_id)
    return {
        "user": {
            "id": user["id"],
            "username": user["username"],
            "is_admin": bool(user["is_admin"]),
            "created_at": user["created_at"],
        },
        "settings": settings,
        "mastery": mastery,
    }


@app.get("/api/admin/users/{user_id}/runs")
async def admin_get_user_runs(
    user_id: int,
    page: int = 1,
    per_page: int = 20,
    admin: dict = Depends(get_admin_user),
):
    from web.service import list_runs
    return await list_runs(user_id, page=page, per_page=per_page)


@app.get("/api/admin/users/{user_id}/reports")
async def admin_get_user_reports(user_id: int, admin: dict = Depends(get_admin_user)):
    from web.database import list_reports_db
    reports = await list_reports_db(user_id)
    return reports


@app.get("/api/admin/users/{user_id}/chats")
async def admin_get_user_chats(user_id: int, admin: dict = Depends(get_admin_user)):
    from web.database import list_chats_by_user
    return await list_chats_by_user(user_id)


@app.get("/api/admin/users/{user_id}/settings")
async def admin_get_user_settings(user_id: int, admin: dict = Depends(get_admin_user)):
    from web.database import get_settings
    return await get_settings(user_id)


@app.get("/api/admin/users/{user_id}/mastery")
async def admin_get_user_mastery(user_id: int, admin: dict = Depends(get_admin_user)):
    from web.database import get_mastery
    return await get_mastery(user_id)


@app.get("/api/admin/users/{user_id}/run/{run_id}")
async def admin_get_run_detail(user_id: int, run_id: str, admin: dict = Depends(get_admin_user)):
    from web.database import load_run
    row = await load_run(run_id, user_id)
    if not row:
        return JSONResponse(status_code=404, content={"error": "运行记录不存在"})
    data = row["data"]
    return data


@app.get("/api/admin/users")
async def admin_users(admin: dict = Depends(get_admin_user)):
    from web.database import get_db
    db = await get_db()
    try:
        cur = await db.execute("SELECT id, username, is_admin, status, created_at FROM users ORDER BY id")
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


@app.get("/api/admin/stats")
async def admin_stats(admin: dict = Depends(get_admin_user)):
    from web.database import get_db
    db = await get_db()
    try:
        row = await db.fetchone("SELECT COUNT(*) AS cnt FROM users")
        user_count = row["cnt"]
        row = await db.fetchone("SELECT COUNT(*) AS cnt FROM runs")
        run_count = row["cnt"]
        row = await db.fetchone("SELECT COUNT(*) AS cnt FROM users WHERE status='pending'")
        pending_count = row["cnt"]
        return {"user_count": user_count, "run_count": run_count, "pending_count": pending_count}
    finally:
        await db.close()


@app.get("/api/admin/pending-users")
async def admin_pending_users(admin: dict = Depends(get_admin_user)):
    from web.database import list_pending_users
    return await list_pending_users()


@app.post("/api/admin/users/{user_id}/approve")
async def admin_approve_user(user_id: int, admin: dict = Depends(get_admin_user)):
    from web.database import approve_user
    ok = await approve_user(user_id)
    if not ok:
        return JSONResponse(status_code=404, content={"detail": "未找到待审批用户"})
    return {"ok": True}


@app.post("/api/admin/users/{user_id}/reject")
async def admin_reject_user(user_id: int, admin: dict = Depends(get_admin_user)):
    from web.database import reject_user
    ok = await reject_user(user_id)
    if not ok:
        return JSONResponse(status_code=404, content={"detail": "未找到待审批用户"})
    return {"ok": True}


@app.delete("/api/admin/users/{user_id}")
async def admin_delete_user(user_id: int, cascade: bool = True, admin: dict = Depends(get_admin_user)):
    if user_id == admin.get("id"):
        return JSONResponse(status_code=400, content={"detail": "不能删除当前登录的管理员账号"})

    from web.database import get_db
    db = await get_db()
    try:
        if cascade:
            await db.execute("DELETE FROM runs WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM chats WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM reports WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM user_settings WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM user_mastery WHERE user_id=?", (user_id,))
        await db.execute("DELETE FROM users WHERE id=?", (user_id,))
        await db.commit()
        return {"ok": True, "cascade": cascade}
    finally:
        await db.close()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("web.app:app", reload=True)


@app.post("/batch/create")
async def batch_create(req: dict = Body(...), user: dict = Depends(get_current_user)):
    items = req.get("items", [])
    settings = req.get("settings", {})
    auto_start = req.get("auto_start", True)
    if not items:
        return JSONResponse(status_code=400, content={"error": "至少需要一道题目"})
    if len(items) > 100:
        return JSONResponse(status_code=400, content={"error": "单次最多 100 道题目"})
    for idx, item in enumerate(items):
        if not item.get("problem_text", "").strip():
            return JSONResponse(status_code=400, content={"error": f"第 {idx + 1} 题缺少题目内容"})
    batch_id = await create_batch(
        user_id=user["id"],
        settings={
            "model": settings.get("model", "qwen/qwen3.6-plus"),
            "subject_id": settings.get("subject_id", "calculus"),
            "mode": settings.get("mode", "workflow_r1"),
            "depth": settings.get("depth", "with_ref"),
        },
        total_count=len(items),
    )
    await add_batch_items(batch_id, items)
    if auto_start:
        await update_batch_status(batch_id, "running")
        get_batch_worker().notify()
    return {"batch_id": batch_id, "total_count": len(items)}


@app.get("/batch/list")
async def batch_list(
    status: str | None = None,
    page: int = 1,
    per_page: int = 20,
    user: dict = Depends(get_current_user),
):
    return await list_batches(user["id"], status=status, page=page, per_page=per_page)


@app.get("/batch/{batch_id}/status")
async def batch_status(batch_id: str, user: dict = Depends(get_current_user)):
    batch = await load_batch(batch_id, user["id"])
    if not batch:
        return JSONResponse(status_code=404, content={"error": "批次不存在"})
    items = await list_batch_items(batch_id)
    total = batch["total_count"]
    done = batch["completed_count"] + batch["failed_count"]
    current_item = next((i for i in items if i["status"] == "running"), None)
    settings_data = json.loads(batch["settings"]) if isinstance(batch["settings"], str) else batch["settings"]
    return {
        "batch_id": batch["id"],
        "status": batch["status"],
        "total_count": total,
        "completed_count": batch["completed_count"],
        "failed_count": batch["failed_count"],
        "progress_percent": int(done / total * 100) if total > 0 else 0,
        "current_item_seq": current_item["seq"] if current_item else None,
        "items": [
            {
                "seq": i["seq"],
                "status": i["status"],
                "problem_preview": i["problem_text"][:60] + ("..." if len(i["problem_text"]) > 60 else ""),
                "run_id": i["run_id"],
                "error_message": i["error_message"],
            }
            for i in items
        ],
        "settings": settings_data,
        "created_at": batch["created_at"],
        "updated_at": batch["updated_at"],
    }


@app.post("/batch/{batch_id}/pause")
async def batch_pause(batch_id: str, user: dict = Depends(get_current_user)):
    batch = await load_batch(batch_id, user["id"])
    if not batch:
        return JSONResponse(status_code=404, content={"error": "批次不存在"})
    if batch["status"] != "running":
        return JSONResponse(status_code=400, content={"error": "只能暂停运行中的批次"})
    await update_batch_status(batch_id, "paused")
    return {"status": "paused"}


@app.post("/batch/{batch_id}/resume")
async def batch_resume(batch_id: str, user: dict = Depends(get_current_user)):
    batch = await load_batch(batch_id, user["id"])
    if not batch:
        return JSONResponse(status_code=404, content={"error": "批次不存在"})
    if batch["status"] not in ("paused", "pending"):
        return JSONResponse(status_code=400, content={"error": "只能继续已暂停或等待中的批次"})
    await update_batch_status(batch_id, "running")
    get_batch_worker().notify()
    return {"status": "running"}


@app.post("/batch/{batch_id}/cancel")
async def batch_cancel(batch_id: str, user: dict = Depends(get_current_user)):
    batch = await load_batch(batch_id, user["id"])
    if not batch:
        return JSONResponse(status_code=404, content={"error": "批次不存在"})
    if batch["status"] not in ("running", "paused"):
        return JSONResponse(status_code=400, content={"error": "只能取消运行中或暂停的批次"})
    await update_batch_status(batch_id, "cancelled")
    items = await list_batch_items(batch_id)
    for item in items:
        if item["status"] == "pending":
            await update_batch_item(batch_id, item["seq"], "cancelled")
    return {"status": "cancelled"}


@app.delete("/batch/{batch_id}")
async def batch_delete(batch_id: str, user: dict = Depends(get_current_user)):
    batch = await load_batch(batch_id, user["id"])
    if not batch:
        return JSONResponse(status_code=404, content={"error": "批次不存在"})
    if batch["status"] == "running":
        return JSONResponse(status_code=400, content={"error": "请先暂停或取消运行中的批次"})
    await delete_batch(batch_id, user["id"])
    return {"status": "deleted"}
