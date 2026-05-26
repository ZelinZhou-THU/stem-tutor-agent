# 异步任务 + 轮询架构设计方案

## 1. 问题背景

### 当前架构

```
用户点击"分析" → POST /analyze/stream → SSE 长连接（阻塞式）
  → 后端执行 LangGraph（parse → reference → verify → diagnose → feedback → review → finalize）
  → 每个节点完成后 yield SSE event + 保存中间状态到 DB
  → 全部完成后 yield result + done
```

### 问题

| 问题 | 影响 |
|------|------|
| Vercel Serverless 有执行时长上限（Hobby: 300s） | 超时后函数被杀，SSE 断开 |
| 长时间阻塞 HTTP 连接 | 占用 Vercel 并发配额 |
| 网络不稳定时 SSE 断开 | 前端虽已支持重连轮询，但后端可能已超时被杀 |
| 单次请求处理全流程 | 无法暂停/恢复/排队 |

### 现有基础设施

- **BatchWorker**：已有后台轮询 + 顺序处理架构，支持暂停/取消
- **DB 持久化**：`_save_intermediate_state()` + `_save_run_payload()` 已在每个节点后保存状态
- **前端轮询**：`showReconnectingUI()` 已实现 3s 轮询 `/analyze/status/{run_id}`，最长 6 分钟
- **取消机制**：`_cancel_events` 字典 + `cancelAnalysis()` 前端按钮

---

## 2. 方案对比

### 方案 A：Fire-and-Forget（asyncio.create_task）

```python
@app.post("/analyze/stream")
async def analyze_stream(...):
    run_id = str(uuid4())
    await _save_running_placeholder(run_id, user_id, ...)
    task = asyncio.create_task(_run_analysis_background(run_id, ...))
    return StreamingResponse(...)  # 只返回 run_id + done
```

**致命问题**：Vercel Serverless 在响应返回后可以立即杀死函数实例。`asyncio.create_task` 创建的后台任务**不会跨请求存活**。此方案不可行。

### 方案 B：复用 BatchWorker（推荐）

将单次分析视为一个大小为 1 的"批处理"，利用已有的 BatchWorker 后台循环执行。

**优点**：
- BatchWorker 在 `startup` 事件中启动，随 Serverless 实例存活
- 已有完整的暂停/取消/错误处理机制
- 已有 `_process_one_cycle()` + `run_stem_tutor_stream()` 的集成
- 前端已有 `/analyze/status/{run_id}` 轮询逻辑
- 最少新代码

**缺点**：
- 批处理场景下 SSE 实时推送不可用（但已有中间状态保存 + 轮询）
- 冷启动时 BatchWorker 需要重新初始化

### 方案 C：外部任务队列（Redis/Celery）

**过于复杂**，需要额外基础设施，不适用于当前 Hobby 计划。

---

## 3. 推荐方案：BatchWorker 复用（详细设计）

### 3.1 核心思路

```
POST /analyze/stream
  → 创建单项目批处理（batch_size=1）
  → 自动启动批处理
  → 立即返回 SSE：{type: "start", run_id, batch_id}
  → 前端进入轮询模式

BatchWorker._loop()
  → 检测到新批处理
  → claim_next_pending_item()
  → 调用 run_stem_tutor_stream()
  → 每个节点完成后 _save_intermediate_state()
  → 全部完成后 _save_run_payload()

前端 showReconnectingUI()
  → 轮询 GET /analyze/status/{run_id}（每 3s）
  → 检测到 "complete" 或 "failed"
  → 调用 GET /analyze/result/{run_id} 获取完整结果
  → 渲染结果
```

### 3.2 数据流

```
┌─────────┐    POST /analyze/stream     ┌──────────┐
│  前端    │ ──────────────────────────→ │  app.py  │
│ (app.js) │                             │          │
│          │ ← SSE: start+run_id         │  创建    │
│          │                             │  batch   │
│          │    GET /analyze/status/{id}  │  (size=1)│
│          │ ──────────────────────────→ │          │
│          │ ← {status: "running"}       │          │
│          │                             └────┬─────┘
│          │                                  │
│          │    GET /analyze/status/{id}  ┌────┴─────┐
│          │ ──────────────────────────→ │BatchWorker│
│          │ ← {status: "running"}       │  _loop()  │
│          │                             │           │
│          │    GET /analyze/status/{id}  │  执行     │
│          │ ──────────────────────────→ │  Graph   │
│          │ ← {status: "complete"}      │  节点     │
│          │                             │  保存DB   │
│          │    GET /analyze/result/{id}  │           │
│          │ ──────────────────────────→ └───────────┘
│          │ ← 完整结果 JSON
│  渲染    │
└─────────┘
```

### 3.3 后端改动

#### 3.3.1 `web/app.py` — 修改 `/analyze/stream` 端点

```python
@app.post("/analyze/stream")
async def analyze_stream(
    problem_text: str = Form(""),
    source_type: str = Form("text"),
    student_solution: str = Form(""),
    model: str = Form("deepseek"),
    subject_id: str = Form("calculus"),
    mode: str = Form("default"),
    depth: int = Form(3),
    image: UploadFile | None = File(None),
    problem_image: UploadFile | None = File(None),
    user: dict = Depends(get_current_user),
):
    user_id = user["id"]

    # OCR 预处理（保持不变）
    ocr_text = ""
    if problem_image:
        img_bytes = await problem_image.read()
        ocr_text, _ = await ocr_problem_text(img_bytes, user_id)
        if ocr_text:
            problem_text = ocr_text

    # 创建单项目批处理
    settings = json.dumps({
        "model": model,
        "subject_id": subject_id,
        "mode": mode,
        "depth": depth,
        "student_solution": student_solution,
        "source_type": source_type,
    })
    batch_id = await create_batch(user_id, settings, total_count=1)
    await add_batch_items(batch_id, [{
        "problem_text": problem_text,
        "student_solution": student_solution,
        "source_type": source_type,
    }])
    await update_batch_status(batch_id, "running")
    get_batch_worker().notify()

    # 立即返回 SSE：仅包含 start 事件
    async def _quick_stream():
        yield f"data: {json.dumps({'type': 'start', 'batch_id': batch_id}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type': 'done', 'message': '任务已提交，正在后台处理...'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(_quick_stream(), media_type="text/event-stream")
```

#### 3.3.2 `web/batch_worker.py` — 增强中间状态传递

当前 `_process_one_cycle()` 已经遍历 `run_stem_tutor_stream()` 的 SSE chunks。需要：

1. 提取 `run_id` 后立即保存到 `batch_items` 表
2. 传递 `student_solution` 和 `source_type` 到分析函数

```python
async def _process_one_cycle(self) -> bool:
    batches = await get_running_batches()
    if not batches:
        return False

    batch = batches[0]
    batch_id = batch["id"]
    settings = json.loads(batch["settings"])
    model = settings.get("model", "deepseek")
    subject_id = settings.get("subject_id", "calculus")
    mode = settings.get("mode", "default")
    depth = settings.get("depth", 3)
    student_solution = settings.get("student_solution", "")
    source_type = settings.get("source_type", "text")

    item = await claim_next_pending_item(batch_id)
    if item is None:
        await update_batch_status(batch_id, "completed")
        return True

    run_id = None
    try:
        async for chunk in run_stem_tutor_stream(
            problem_text=item["problem_text"],
            source_type=source_type,
            student_solution=student_solution,
            model=model,
            subject_id=subject_id,
            mode=mode,
            depth=depth,
            user_id=batch["user_id"],
        ):
            # 提取 run_id
            if run_id is None:
                data = _parse_sse_data(chunk)
                if data and data.get("type") == "result":
                    run_id = data.get("data", {}).get("run_id") or \
                             data.get("data", {}).get("run_meta", {}).get("run_id")

            # 检查暂停/取消
            fresh = await load_batch(batch_id, batch["user_id"])
            if fresh and fresh["status"] in ("paused", "cancelled"):
                break

        if run_id:
            await update_batch_item(batch_id, item["seq"], "completed", run_id=run_id)
        else:
            await update_batch_item(batch_id, item["seq"], "failed", error_message="no run_id returned")
    except Exception as exc:
        await update_batch_item(batch_id, item["seq"], "failed", error_message=str(exc)[:200])

    return True
```

#### 3.3.3 `web/app.py` — 新增批处理状态代理端点

前端需要通过 `batch_id` 轮询，获取对应 `run_id` 后切换到 `run_id` 轮询：

```python
@app.get("/analyze/batch-status/{batch_id}")
async def batch_analyze_status(batch_id: str, user: dict = Depends(get_current_user)):
    batch = await load_batch(batch_id, user["id"])
    if not batch:
        raise HTTPException(404, "Batch not found")

    items = await list_batch_items(batch_id)
    item = items[0] if items else None

    if item and item["run_id"]:
        run_status = await _get_run_status(item["run_id"], user["id"])
        return {"status": run_status.get("status", "running"), "run_id": item["run_id"], **run_status}
    elif item and item["status"] == "running":
        return {"status": "running", "run_id": None}
    elif item and item["status"] == "failed":
        return {"status": "failed", "error": item.get("error_message", "Unknown error")}
    else:
        return {"status": "pending"}
```

### 3.4 前端改动

#### 3.4.1 `app.js` — 修改 `startStreamWithReconnect()`

```javascript
async function startStreamWithReconnect(formData) {
    try {
        const resp = await fetch("/analyze/stream", {
            method: "POST",
            body: formData,
            headers: { Authorization: `Bearer ${token}` },
        });

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let batchId = null;

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split("\n");
            buffer = lines.pop();

            for (const line of lines) {
                if (!line.startsWith("data: ")) continue;
                const data = JSON.parse(line.slice(6));

                if (data.type === "start") {
                    batchId = data.batch_id;
                    showAnalyzingUI();  // 显示"正在分析中..."
                }
            }
        }

        // SSE 结束 → 进入轮询模式
        if (batchId) {
            pollBatchUntilComplete(batchId);
        }
    } catch (err) {
        // 网络错误 → 已有的重连逻辑
        if (runId) {
            showReconnectingUI(runId);
        }
    }
}
```

#### 3.4.2 `app.js` — 新增 `pollBatchUntilComplete()`

```javascript
async function pollBatchUntilComplete(batchId, maxAttempts = 120, interval = 3000) {
    for (let i = 0; i < maxAttempts; i++) {
        await new Promise(r => setTimeout(r, interval));

        const resp = await fetch(`/analyze/batch-status/${batchId}`, {
            headers: { Authorization: `Bearer ${token}` },
        });

        if (!resp.ok) continue;
        const data = await resp.json();

        if (data.status === "complete" || data.status === "needs_review") {
            const result = await loadResultAndRender(data.run_id);
            return;
        }

        if (data.status === "failed" || data.status === "unavailable") {
            showError(data.error || "分析失败");
            return;
        }

        updateProgressIndicator(i, maxAttempts);  // 可选：显示进度
    }

    showError("分析超时，请稍后查看历史记录");
}
```

### 3.5 保留实时 SSE 的混合模式（可选增强）

如果未来需要恢复实时进度推送，可以使用 **DB 轮询 + 增量渲染**：

```
BatchWorker 执行每个节点后：
  _save_intermediate_state() → DB

前端轮询 /analyze/status/{run_id}：
  返回 running + 最新完成的节点列表
  前端渲染已完成的节点（复用 renderPartial()）
```

这不需要 SSE，但能达到类似的实时体验。代价是 3s 的轮询延迟，对教育场景完全可接受。

---

## 4. 实施计划

### Phase 1（已完成）：紧急修复
- [x] Fix 1: service.py 异常日志
- [x] Fix 2: generate_review_problems 优雅降级
- [x] Fix 3: vercel.json maxDuration: 300

### Phase 2：异步化改造（本方案）
- [ ] 4.1 修改 `app.py` `/analyze/stream` → 创建单项目批处理
- [ ] 4.2 增强 `batch_worker.py` 传递 student_solution
- [ ] 4.3 新增 `/analyze/batch-status/{batch_id}` 端点
- [ ] 4.4 修改前端 `startStreamWithReconnect()` + 新增 `pollBatchUntilComplete()`
- [ ] 4.5 测试：本地 SQLite + Vercel PG 环境

### Phase 3：增强（可选）
- [ ] 中间状态轮询 + 增量渲染
- [ ] 批处理分析进度条
- [ ] WebSocket 替代轮询（需要 Vercel Pro）

---

## 5. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 冷启动时 BatchWorker 未运行 | startup 事件中启动，前端轮询会等到它开始处理 |
| Serverless 实例被回收时任务中断 | `_save_intermediate_state()` 每个节点保存，`recover_stale_running_items()` 重置未完成任务 |
| 前端轮询频率过高 | 3s 间隔，120 次（6 min），与现有 `showReconnectingUI` 一致 |
| 批处理按 FIFO 排队，单分析需等待 | BatchWorker 每 5s 轮询，延迟可接受；未来可增加并发处理 |
| settings JSON 字段耦合 | 新增 `source_type`、`student_solution` 字段，向后兼容（有默认值） |
