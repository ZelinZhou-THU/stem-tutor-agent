# 批量分析队列功能设计

**日期：** 2026-05-22
**状态：** Draft

---

## 1. 功能概述

支持学生一次性提交多道题目，系统串行排队自动分析。学生可以关闭浏览器，后台继续处理。回来后查看进度和结果。

### 核心需求

- 提交 1-50+ 道题目，暂存到队列
- 后端 worker 串行逐题分析，不依赖浏览器
- 前端展示队列列表 + 进度条
- 支持暂停/继续/取消
- 结果进入历史记录，同时有批量汇总视图
- 断点续传：服务重启后恢复未完成任务

---

## 2. 数据库设计

### 2.1 batches 表

```sql
CREATE TABLE IF NOT EXISTS batches (
    id TEXT PRIMARY KEY,                -- UUID
    user_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
        -- pending: 刚创建，等待开始
        -- running: worker 正在处理
        -- paused:  用户暂停
        -- completed: 全部完成
        -- cancelled: 用户取消
    total_count INTEGER NOT NULL DEFAULT 0,
    completed_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    settings TEXT NOT NULL DEFAULT '{}',  -- JSON: model, subject_id, mode, depth
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

### 2.2 batch_items 表

```sql
CREATE TABLE IF NOT EXISTS batch_items (
    id TEXT PRIMARY KEY,                -- UUID
    batch_id TEXT NOT NULL,
    seq INTEGER NOT NULL,               -- 题目顺序（从 0 开始）
    status TEXT NOT NULL DEFAULT 'pending',
        -- pending: 等待处理
        -- running: 正在分析
        -- completed: 分析完成
        -- failed: 分析失败
        -- skipped: 被跳过
        -- cancelled: 被取消
    problem_text TEXT NOT NULL,
    student_solution TEXT NOT NULL DEFAULT '',
    source_type TEXT NOT NULL DEFAULT 'text',
    run_id TEXT,                        -- 关联 runs 表的分析结果
    error_message TEXT,                 -- 失败时的错误信息
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (batch_id) REFERENCES batches(id)
);

CREATE INDEX IF NOT EXISTS idx_batch_items_batch ON batch_items(batch_id, seq);
CREATE INDEX IF NOT EXISTS idx_batches_user ON batches(user_id, created_at DESC);
```

### 设计要点

- `batch_items.run_id` 关联到现有 `runs` 表，每道题的分析结果就是一条正常的 run 记录
- `batches.settings` 存储批次级别的公共设置（model、subject、mode、depth），每道题继承这些设置
- 不修改现有 `runs` 表结构。run 记录通过 `batch_items.run_id` 反向关联到批次

---

## 3. 后端架构

### 3.1 BatchWorker（新文件 `web/batch_worker.py`）

```python
class BatchWorker:
    """单例后台 worker，串行处理所有用户的批次任务。"""

    def __init__(self):
        self._task: asyncio.Task | None = None
        self._running = False
        self._current_batch_id: str | None = None
        self._current_item_id: str | None = None

    async def start(self):
        """启动后台协程。"""

    async def stop(self):
        """优雅停止（完成当前题后停止）。"""

    def notify(self):
        """通知 worker 有新任务。"""

    async def _loop(self):
        """主循环：取 pending batch → 取 pending item → 执行分析 → 更新状态。"""
```

**工作流程：**

1. `_loop` 是一个无限 `while True` 循环
2. 每次循环查询 DB：`SELECT * FROM batches WHERE status = 'running' AND completed_count < total_count ORDER BY created_at LIMIT 1`
3. 找到批次后，取其第一个 pending item
4. 调用 `run_stem_tutor_stream()` 执行分析（消费 SSE 流直到 `done`）
5. 将 run_id 写入 batch_item，标记 completed
6. 更新 batches 的 completed_count / failed_count
7. 所有 item 完成后标记 batch 为 completed
8. 没有待处理任务时，`await asyncio.sleep(2)` 后重试

**取消支持：** 复用现有 `_cancel_events` dict。worker 在每题开始前检查 batch.status 是否变为 `paused` 或 `cancelled`。

**崩溃恢复：** 服务重启时，`BatchWorker.start()` 自动恢复所有 `status='running'` 的批次中的 pending items。如果某个 item 是 `status='running'`（说明崩溃时正在处理），将其标记为 failed 后继续。

### 3.2 Worker 如何调用 run_stem_tutor_stream

`run_stem_tutor_stream` 是 async generator，worker 直接消费：

```python
async for chunk in run_stem_tutor_stream(
    problem_text=item.problem_text,
    raw_student_solution=item.student_solution,
    source_type=item.source_type,
    model_name=settings.model,
    subject_id=settings.subject_id,
    mode=settings.mode,
    depth=settings.depth,
    user_id=batch.user_id,
):
    # 只关心最终结果，忽略 SSE 事件
    if '"type": "result"' in chunk:
        # 解析 final response，提取 run_id
        pass
    if '"type": "done"' in chunk:
        break
```

**零改动现有代码**。`run_stem_tutor_stream` 内部已经处理了所有状态保存（`_save_run_payload`、`_save_intermediate_state`），worker 只需消费。

### 3.3 API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `POST /batch/create` | POST | 创建批次，接收 items 数组 + settings |
| `GET /batch/list` | GET | 列出当前用户的批次（分页） |
| `GET /batch/{batch_id}/status` | GET | 获取批次详情 + 所有 items 状态 |
| `POST /batch/{batch_id}/pause` | POST | 暂停（当前题完成后停止） |
| `POST /batch/{batch_id}/resume` | POST | 继续 |
| `POST /batch/{batch_id}/cancel` | POST | 取消（取消当前题 + 标记剩余为 skipped） |
| `DELETE /batch/{batch_id}` | DELETE | 删除批次及其 items（不删除已完成的 runs） |

**`POST /batch/create` 请求体：**

```json
{
  "items": [
    {"problem_text": "...", "student_solution": "...", "source_type": "text"},
    {"problem_text": "...", "student_solution": "...", "source_type": "text"}
  ],
  "settings": {
    "model": "qwen/qwen3.6-plus",
    "subject_id": "calculus",
    "mode": "workflow_r1",
    "depth": "standard"
  },
  "auto_start": true
}
```

**`GET /batch/{batch_id}/status` 响应：**

```json
{
  "batch_id": "uuid",
  "status": "running",
  "total_count": 10,
  "completed_count": 3,
  "failed_count": 1,
  "progress_percent": 40,
  "current_item_seq": 4,
  "items": [
    {
      "seq": 0,
      "status": "completed",
      "problem_preview": "计算积分...",
      "run_id": "uuid",
      "error_message": null
    },
    {
      "seq": 1,
      "status": "failed",
      "problem_preview": "求极限...",
      "run_id": null,
      "error_message": "timeout"
    },
    {
      "seq": 4,
      "status": "running",
      "problem_preview": "求导数...",
      "run_id": null,
      "error_message": null
    }
  ],
  "settings": {"model": "...", "subject_id": "...", ...},
  "created_at": "ISO8601",
  "updated_at": "ISO8601"
}
```

### 3.4 FastAPI 集成

在 `app.py` 的 `lifespan` 中启动/停止 worker：

```python
@asynccontextmanager
async def lifespan(app):
    worker = get_batch_worker()
    await worker.start()
    yield
    await worker.stop()

app = FastAPI(lifespan=lifespan)
```

---

## 4. 前端设计

### 4.1 新增 `#queue` 页面

在侧边栏新增"批量队列"导航项，`#queue` 路由对应新页面。

### 4.2 页面布局

```
┌─────────────────────────────────────────┐
│ 批量分析队列                              │
├─────────────────────────────────────────┤
│ [+ 新建批次]           筛选: ▼ 全部状态    │
├─────────────────────────────────────────┤
│ ┌─ 批次 #abc123 ──────────────────────┐ │
│ │ 微积分 | 标准 | 2026-05-22 14:30     │ │
│ │ ████████████░░░░░░░ 7/10 (70%)      │ │
│ │ ▶ 继续   ⏸ 暂停   ✖ 取消   🗑 删除  │ │
│ └─────────────────────────────────────┘ │
│                                         │
│ ┌─ 批次 #def456 ──────────────────────┐ │
│ │ 线性代数 | 快速 | 2026-05-22 10:15   │ │
│ │ ████████████████████ 10/10 (100%)   │ │
│ │ ✅ 已完成   📋 查看汇总   🗑 删除     │ │
│ └─────────────────────────────────────┘ │
└─────────────────────────────────────────┘
```

### 4.3 新建批次流程

点击"新建批次"打开模态框：

1. **设置区**：选择学科、模式、深度、模型（复用现有表单的下拉组件样式）
2. **题目输入区**：可扩展的题目列表
   - 每道题有：题号、题目文本框（支持拍照/上传图片 OCR）、学生解答文本框（支持拍照/上传图片 OCR）、删除按钮
   - OCR 流程与现有单题分析一致：点击拍照/上传按钮 → 调用 `POST /ocr` → 结果填入文本框 → 用户可编辑调整
   - "添加题目"按钮追加新行
   - 支持粘贴多题（以空行分隔，系统自动拆分）
3. **底部**：题目数量统计 + "提交"按钮

**关键设计**：OCR 在提交前完成，提交时所有题目都已是文本。批量分析时不再做 OCR，worker 只处理文本输入。这样用户可以在提交前检查和修正 OCR 结果，也避免了 worker 需要处理图片数据的复杂性。

### 4.4 批次详情页（点击批次卡片展开）

展示每道题的状态列表：

```
┌─────────────────────────────────────────┐
│ #1 ✅ 已完成  计算积分 ∫_0^1 x²dx      │
│ #2 ✅ 已完成  求极限 lim(x→0) sin(x)/x │
│ #3 ❌ 失败   求导数 y=...  (timeout)   │
│ #4 🔄 分析中  证明题：...              │
│ #5 ⏳ 排队中  计算行列式...              │
│ #6 ⏳ 排队中  矩阵乘法...               │
└─────────────────────────────────────────┘
```

点击已完成的题目跳转到历史记录中的对应 run 详情。

### 4.5 轮询机制

- 批次状态为 `running` 时，每 5 秒轮询 `GET /batch/{id}/status`
- 批次 `completed`/`paused`/`cancelled` 时停止轮询
- 用户在 `#queue` 页面时才轮询，离开页面停止

### 4.6 结果查看

**批量汇总**：批次详情底部增加"汇总统计"，展示：
- 正确/错误步骤数统计
- 错误类型分布（饼图/条形图）
- 薄弱知识点 Top 5

**单题详情**：点击已完成的题目，复用现有的 `renderResults()` 渲染完整的分析结果。

---

## 5. 路由与导航

### 5.1 新增路由

| Hash 路由 | 页面 | 侧边栏 |
|-----------|------|--------|
| `#queue` | 批量队列 | "批量队列" (新增) |

路由注册在 `app.js` 的 `AppRouter.pages` 数组中，新增 `"queue"` 项。

### 5.2 侧边栏顺序

分析诊断 → **批量队列** → 历史记录 → 统计概览 → 学习报告 → 调试日志 → 设置 → 管理员面板

---

## 6. 错误处理

| 场景 | 处理方式 |
|------|----------|
| 单题分析失败 | 标记 item 为 failed，继续下一题 |
| Worker 崩溃 | 服务重启时自动恢复，running item 标记为 failed |
| 用户暂停 | 当前题完成后停止，剩余 item 保持 pending |
| 用户取消 | 取消当前分析（via cancel_event），剩余标记 skipped |
| 批次全部失败 | 标记 batch completed，failed_count = total_count |
| 网络中断 | 每题独立重试（复用 run_stem_tutor_stream 的内置重试） |

---

## 7. 并发控制

- **单 worker 串行**：全局只有一个 BatchWorker 实例，同一时刻只处理一道题
- **多用户公平性**：按 batch created_at 排序，先提交先处理（FIFO）
- **不限制用户提交数量**：用户可以创建多个批次，按时间顺序排队

未来如果需要并发，可以将 worker 改为 semaphore 控制的多协程，当前不需要。

---

## 8. 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `web/database.py` | 新增 | batches/batch_items 表 DDL + CRUD（~100 行） |
| `web/batch_worker.py` | 新建 | BatchWorker 类（~120 行） |
| `web/app.py` | 新增 | 7 个 batch API 端点 + lifespan 修改（~100 行） |
| `web/service.py` | 不改 | 复用 run_stem_tutor_stream |
| `web/templates/index.html` | 新增 | #queue section + 新建批次模态框（含 OCR 上传按钮）（~150 行） |
| `web/static/app.js` | 新增 | QueueModule + OCR 触发 + 路由注册（~300 行） |
| `web/static/style.css` | 新增 | 队列样式（~100 行） |
| `tests/test_batch.py` | 新建 | 数据库 CRUD + worker 逻辑测试（~120 行） |

**总计约 960 行新代码，0 行改动现有代码。**

---

## 9. 不在范围内（Out of Scope）

- WebSocket 实时推送（轮询足够）
- 并行分析（串行足够）
- 批量汇总统计图表（V1 只展示列表，图表后续迭代）
- 导出批量结果为 PDF/Excel
