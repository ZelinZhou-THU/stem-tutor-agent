# stem-tutor-agent

基于 LangGraph + SymPy 的 STEM 智能辅导 Agent。输入一道题目与学生的分步解题过程，系统自动完成分步验证、错误诊断与个性化学习反馈。

## 功能特性

| 功能 | 说明 |
|------|------|
| 分步验证 | 逐步验证学生解答，标注 `correct` / `incorrect_math` / `inconsistent_or_unsupported` / `unclear` |
| 多策略验证链 | SymPy 符号验证 → 数值采样 → Agent 工具调用 → LLM 文本验证 → 规则兜底，按优先级依次尝试 |
| 错误诊断 | 基于结构化错误分类体系定位根因，输出错误码与证据链 |
| 学习反馈 | 面向学生的简洁反馈，包含复习概念与下一步行动建议 |
| 复习题生成 | 基于薄弱点自动生成 1-3 道针对性练习题 |
| 8 学科支持 | 微积分、线性代数、力学、电磁学、光学、量子力学、相对论、热力学 |
| Agent 工具链 | LangGraph Agent 子图可调用 21 个工具（8 微积分 + 11 线性代数 + 1 Python 沙箱 + 1 批量流水线），实现计算闭环验证 |
| OCR 识别 | 支持上传图片，通过视觉模型自动转录题目与解题过程 |
| 裁剪 OCR | Cropper.js 图片裁剪后识别，支持拖拽上传与拍照 |
| LaTeX 预览 | OCR 结果实时 KaTeX 渲染预览，手动编辑同步更新 |
| 流式输出 | Server-Sent Events 实时推送分析进度 |
| 对话追问 | 分析完成后可继续对话，追问解题细节 |
| 学习报告 | 聚合多次诊断结果，生成跨题学习报告，识别知识盲区与改进趋势 |
| 批量分析队列 | 一次提交多道题目，后台串行处理，支持暂停/恢复/取消，进度轮询 |
| 预算管理 | 全局预算池 + 按节点时间配额，支持 `quick` / `standard` / `thorough` 三档深度 |
| 用户账户系统 | 注册/登录/会话管理，JWT 令牌认证，用户设置与掌握度数据持久化 |
| 管理员面板 | 系统用户管理，查看任意用户的运行记录/报告/聊天/设置与掌握度数据 |
| 自动管理员创建 | 首次启动自动创建 admin 账户（admin / admin123）|

## 快速开始

### 环境要求

- Python 3.10+（推荐 3.11）

### 安装

```bash
pip install -e .
```

### 运行 Web 界面（推荐）

```bash
python -m web.app
```

启动后访问 http://localhost:8000，支持文本输入、OCR 图片上传（裁剪+拖拽）、实时流式分析、自动学科检测、对话追问与学习报告。批量分析队列支持一次提交多道题目。

首次启动自动创建管理员账户：`admin / admin123`。普通用户需在登录页面注册后使用。

### 一键公网访问

Windows 双击 `start.bat` 可同时启动本地服务与 Cloudflare Tunnel，自动生成公网访问地址（需安装 [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)）。

### 命令行运行

```bash
# Mock 模式（无需 API Key）
python -m cli.main --input fixtures/sample_case.json --provider mock

# 真实模型 API
python -m cli.main --input fixtures/sample_case.json --provider real --health-check
```

### 运行测试

```bash
pytest -q
```

所有测试（24 个文件）使用 `tmp_path` + `monkeypatch` 隔离数据库，不依赖真实 LLM 服务。

## 配置说明

项目优先读取工作区根目录的 `key.env` 文件（参考 `key.env.example`），也支持环境变量覆盖。

### 基础配置

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `STEM_TUTOR_PROVIDER` | Provider 类型（`mock` / `openai-compatible`） | `mock` |
| `STEM_TUTOR_SUBJECT` | 默认学科 | `calculus` |
| `PARATERA_API_KEY` | LLM API 密钥 | （空） |
| `PARATERA_URL` | LLM API 地址 | （空） |

### 模型配置

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `STEM_TUTOR_REASONING_MODEL` | 推理模型（参考解答生成等） | `qwen/qwen3.6-plus` |
| `STEM_TUTOR_FAST_MODEL` | 快速模型（验证、诊断、反馈等） | `deepseek/deepseek-v3.2` |
| `STEM_TUTOR_OCR_MODEL` | OCR 视觉模型 | `qwen/qwen3.6-plus` |
| `STEM_TUTOR_BASELINE_GLM5_MODEL` | 基线对比模型（GLM5） | `qwen/qwen3-30b-a3b-instruct-2507` |
| `STEM_TUTOR_BASELINE_KIMI_MODEL` | 基线对比模型（Kimi） | `qwen/qwen3-30b-a3b-instruct-2507` |
| `STEM_TUTOR_DETECTION_MODEL` | 学科检测模型 | `qwen/qwen3-30b-a3b-instruct-2507` |
| `STEM_TUTOR_VERIFY_MODEL_GROUP` | 验证使用的模型组 | `fast` |
| `STEM_TUTOR_VERIFY_MODEL` | 验证模型覆盖（空则使用模型组） | （空） |

### 功能开关与参数

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `STEM_TUTOR_SYMPY_ENABLED` | 启用 SymPy 符号验证 | `true` |
| `STEM_TUTOR_SYMPY_TIMEOUT` | SymPy 计算超时（秒） | `3.0` |
| `STEM_TUTOR_TOOL_CALLING` | 启用 Agent 工具调用 | `false` |
| `STEM_TUTOR_DUAL_MODEL` | 启用双模型 Agent 模式（独立工具调用模型） | `false` |
| `STEM_TUTOR_BUDGET_ENABLED` | 启用全局预算管理（按节点时间配额与预算池） | `false` |
| `STEM_TUTOR_LOAD_LEGACY_TOOLS` | 加载完整工具集 | `false` |
| `STEM_TUTOR_PYTHON_EXECUTABLE` | Python 沙箱解释器路径（空则自动检测 conda LLM 环境） | （空） |
| `STEM_TUTOR_PYTHON_TIMEOUT` | Python 沙箱超时（秒） | `10.0` |
| `STEM_TUTOR_TIMEOUT` | 请求超时（秒） | `300` |
| `STEM_TUTOR_MAX_RETRIES` | 最大重试次数 | `1` |
| `STEM_TUTOR_ALLOW_MOCK_FALLBACK` | 允许降级到 mock | `true` |
| `STEM_TUTOR_DEPTH` | 分析深度（`quick` / `standard` / `thorough`） | `standard` |
| `STEM_TUTOR_SIMPLE_FASTPATH` | 启用简单问题快速路径 | `true` |
| `STEM_TUTOR_DETERMINISTIC_VERIFY` | 启用确定性验证优先 | `true` |
| `STEM_TUTOR_REFERENCE_MAX_TOOL_ROUNDS` | 参考解答最大工具轮次 | `1` |
| `STEM_TUTOR_AGENT_REQUEST_TIMEOUT` | Agent 请求超时（秒） | `45` |
| `STEM_TUTOR_AGENT_MAX_DURATION` | Agent 最大持续时间（秒） | `90` |
| `STEM_TUTOR_HINT_MAX_CHARS` | 计算提示最大字符数 | `1200` |
| `STEM_TUTOR_INCLUDE_FAILED_HINTS` | 在提示中包含失败工具结果 | `false` |
| `STEM_TUTOR_TOOL_RESULT_MAX_CHARS` | 工具结果截断字符数 | `200` |
| `STEM_TUTOR_NODE_TIMING` | 启用节点级计时追踪 | `true` |
| `STEM_TUTOR_PARALLEL_REVIEW` | 启用并行复习题生成 | `true` |

## 项目架构

### 目录结构

```
stem-tutor-agent/
├── stem_tutor/
│   ├── domain/              # Pydantic 数据模型
│   ├── graph/               # LangGraph 状态定义与流程编排
│   │   ├── state.py         #   全局状态 TutorGraphState
│   │   ├── workflow.py      #   主图构建与运行
│   │   ├── budget.py        #   按节点时间预算管理
│   │   ├── global_budget.py #   跨节点全局预算池
│   │   ├── agent_subgraph.py#   Agent 子图（工具调用）
│   │   ├── strategy.py      #   多策略验证链
│   │   └── observability.py #   Provider 调用追踪与不确定性标记
│   ├── nodes/               # 业务节点实现
│   │   ├── ocr_preprocess.py          # OCR 图片预处理
│   │   ├── parse_student_solution.py  # 学生解答步骤拆分
│   │   ├── generate_reference_solution.py # LLM 参考解答生成
│   │   ├── verify_steps.py            # 分步验证（核心节点）
│   │   ├── diagnose_error.py          # 错误根因诊断
│   │   ├── generate_feedback.py       # 学习反馈生成
│   │   ├── generate_review_problems.py# 复习题生成
│   │   ├── complexity_gate.py         # 题目复杂度分类
│   │   └── finalize_report.py         # 最终报告组装
│   ├── prompts/             # 提示词模板
│   ├── providers/           # LLM Provider 抽象层
│   │   ├── base.py          #   LLMProvider 抽象基类
│   │   ├── factory.py       #   create_provider() 工厂
│   │   ├── mock_provider.py #   确定性 Mock Provider
│   │   └── openai_compatible_provider.py # OpenAI 兼容 API Provider
│   ├── subjects/            # 学科配置（8 个 YAML）与自动检测
│   ├── taxonomy/            # 错误分类体系
│   ├── tools/               # Agent 计算工具
│   │   ├── execute_python.py  #  通用 Python 沙箱执行
│   │   ├── calculus_tools.py  #  微积分工具（求导/积分/极限/级数/方程/ODE）
│   │   ├── matrix_tools.py    #  线性代数工具（矩阵运算/特征值/求解）
│   │   └── tool_utils.py      #  共享工具函数
│   ├── evaluation/          # 评估框架
│   ├── settings.py          # 配置加载（环境变量 + key.env）
│   └── sympy_verify.py      # SymPy 符号验证引擎
├── web/
│   ├── app.py               # FastAPI 路由定义（~650 行）
│   ├── service.py           # 业务服务层
│   ├── models.py            # 请求/响应 Pydantic 模型
│   ├── database.py          # SQLite 数据库 CRUD（8 张表）
│   ├── batch_worker.py      # 批量分析后台队列 Worker
│   ├── auth.py              # JWT 认证（bcrypt + python-jose）
│   ├── templates/           # 前端 HTML 模板
│   └── static/              # CSS / JS 静态资源
├── cli/
│   ├── main.py              # 命令行入口
│   └── evaluate.py          # 评估 CLI
├── fixtures/                # 测试样例
├── tests/                   # 单元与集成测试（24 个测试文件）
└── logs/                    # 运行日志与报告（gitignore）
```

### 核心工作流

系统基于 LangGraph StateGraph 编排，包含 8 个核心节点：

```
OCR预处理 ─→ 解析步骤 ─→ 生成参考解答 ─→ 验证步骤 ─→ 诊断错误 ─→ 生成反馈 ─→ 生成复习题 ─→  finalize
   (可选)    步骤拆分     +Agent工具调用    多策略链     根因分析     学习建议      练习题      报告组装
             与标准化     +关键断言提取    +SymPy验证   +分类体系    +复习概念     +薄弱点
```

**路由策略：**
- **解析失败** → 提前结束，写入失败原因
- **全部正确** → 跳过诊断，直接生成反馈
- **存在错误** → 进入诊断节点
- **低置信度过高** → 标记 `manual_review_required`，避免误导

**Agent 子图：** 参考解答生成和验证节点可启用 LangGraph Agent 子图，通过工具调用链执行 SymPy / Python 计算，支持双模型模式（独立工具调用模型 + 推理模型）。

### 数据库模式

SQLite 数据库（`data/stem_tutor.db`）包含 8 张表：

| 表 | 说明 |
|------|------|
| `users` | 用户账户（id, username, password_hash, is_admin, created_at） |
| `runs` | 分析运行记录（JSON data, status, subject, problem_text） |
| `chats` | 对话历史（按 run_id 关联，messages 为 JSON 数组） |
| `reports` | 学习报告（JSON data, title） |
| `user_settings` | 用户偏好设置（JSON） |
| `user_mastery` | 用户掌握度数据（JSON） |
| `batches` | 批量分析批次（status, settings_json, 计数统计） |
| `batch_items` | 批次内题目（problem_text, student_solution, status, run_id） |

### Agent 工具链

Agent 子图可调用以下计算工具：

| 模块 | 工具 | 说明 |
|------|------|------|
| **通用** | `execute_python` | 沙箱 Python 子进程执行 |
| **微积分** | `compute_derivative` | 符号求导 |
| | `compute_integral` | 定积分 / 不定积分 |
| | `compute_limit` | 极限计算 |
| | `compute_series` | Taylor 展开 |
| | `solve_equation` | 方程求解 |
| | `solve_ode` | ODE 求解 |
| | `simplify_expression` | 表达式化简 |
| | `compute_pipeline` | 批量多步计算（支持 `$1`, `$2` 引用前序结果） |
| **线性代数** | `matrix_multiply` | 矩阵乘法 |
| | `matrix_add` | 矩阵加法 |
| | `matrix_inverse` | 矩阵求逆 |
| | `matrix_determinant` | 行列式 |
| | `matrix_eigenvalues` | 特征值 |
| | `matrix_eigenvectors` | 特征向量 |
| | `matrix_rank` | 矩阵秩 |
| | `matrix_rref` | 行最简形 |
| | `matrix_transpose` | 转置 |
| | `matrix_trace` | 迹 |
| | `solve_linear_system` | 线性方程组求解 |

## Web API

### 分析接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/analyze` | 同步分析，返回完整结果 |
| `POST` | `/analyze/stream` | 流式分析，SSE 实时推送进度 |
| `GET` | `/analyze/status/{run_id}` | 查询运行状态 |
| `GET` | `/analyze/result/{run_id}` | 获取运行结果 |
| `POST` | `/analyze/cancel/{run_id}` | 取消正在运行的分析 |
| `POST` | `/api/verify-step` | 单步重验证（针对低置信度步骤） |

### 对话追问

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/chat/stream` | 流式对话追问（SSE） |
| `GET` | `/chat/history/{run_id}` | 获取对话历史 |

### 学习报告

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/report/generate` | 生成学习报告（SSE 流式） |
| `GET` | `/report/data` | 获取报告数据（支持日期筛选） |
| `GET` | `/report/runs` | 列出可用于生成报告的运行记录 |
| `GET` | `/report/list` | 分页列出已生成的报告 |
| `GET` | `/report/{report_id}` | 获取报告详情 |
| `DELETE` | `/report/{report_id}` | 删除报告 |

### 运行管理

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/history` | 运行历史列表（支持分页与筛选） |
| `GET` | `/stats` | 聚合统计信息 |
| `DELETE` | `/api/runs` | 批量删除运行记录 |
| `POST` | `/api/runs/cleanup` | 清理 N 天前的运行记录 |

### 辅助接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/ocr` | 图片 OCR 识别 |
| `POST` | `/detect-subject` | 自动检测题目学科 |

### 批量分析队列

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/batch/create` | 创建批量分析（含题目列表与分析设置） |
| `GET` | `/batch/list` | 列出当前用户的批次 |
| `GET` | `/batch/{batch_id}/status` | 查询批次状态与进度 |
| `POST` | `/batch/{batch_id}/pause` | 暂停批次 |
| `POST` | `/batch/{batch_id}/resume` | 恢复批次 |
| `POST` | `/batch/{batch_id}/cancel` | 取消批次 |
| `DELETE` | `/batch/{batch_id}` | 删除批次 |

### 用户认证

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/auth/register` | 注册新用户 |
| `POST` | `/api/auth/login` | 登录获取 JWT 令牌 |
| `GET` | `/api/auth/me` | 获取当前用户信息 |

### 用户设置与掌握度

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/user/settings` | 获取用户偏好设置 |
| `POST` | `/api/user/settings` | 保存用户偏好设置 |
| `GET` | `/api/user/mastery` | 获取用户掌握度数据 |
| `POST` | `/api/user/mastery` | 更新用户掌握度数据 |

### 管理员接口（需 admin 权限）

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/admin/users` | 列出所有用户 |
| `GET` | `/api/admin/stats` | 系统统计概览 |
| `DELETE` | `/api/admin/users/{user_id}` | 删除用户（支持级联删除） |
| `GET` | `/api/admin/users/{user_id}` | 用户基本信息 + 设置 + 掌握度 |
| `GET` | `/api/admin/users/{user_id}/runs` | 用户的运行记录（分页） |
| `GET` | `/api/admin/users/{user_id}/reports` | 用户的学习报告 |
| `GET` | `/api/admin/users/{user_id}/chats` | 用户的聊天记录 |
| `GET` | `/api/admin/users/{user_id}/settings` | 用户设置详情 |
| `GET` | `/api/admin/users/{user_id}/mastery` | 用户掌握度详情 |
| `GET` | `/api/admin/users/{user_id}/run/{run_id}` | 运行详情（含原始输出） |

### 流式响应示例

`/analyze/stream` 返回 Server-Sent Events：

```
data: {"type": "start", "run_id": "...", "message": "开始分析"}
data: {"type": "node_start", "node": "parse_student_solution", "label": "解析解题步骤"}
data: {"type": "progress", "node": "parse_student_solution", "detail": "解析到 5 个解题步骤"}
data: {"type": "node_done", "node": "parse_student_solution", "label": "解析解题步骤", "partial": {...}}
...
data: {"type": "result", "data": {...}}
data: {"type": "done", "message": "分析完成"}

# 用户取消时：
data: {"type": "cancelled", "message": "分析已取消"}
```

## 数据模型

| 模型 | 用途 |
|------|------|
| `ProblemInput` | 题目输入（支持 text / ocr 来源） |
| `SolutionStep` | 学生解题步骤 |
| `VerificationResult` | 分步验证结果（标签 / 证据 / 置信度 / SymPy 标记） |
| `VerificationLabel` | 验证标签枚举：`correct` / `incorrect_math` / `inconsistent_or_unsupported` / `unclear` |
| `ErrorDiagnosis` | 错误诊断（错误码 / 类别 / 根因假设 / 证据 / 置信度） |
| `FeedbackReport` | 学习反馈报告 |
| `ReviewProblem` | 复习题 |
| `ReferenceSolutionPayload` | 参考解答输出（文本 + 关键断言） |
| `VerificationPayload` | 轻量验证载荷（供 Agent 子图结构化输出） |
| `DiagnosisPayload` | 轻量诊断载荷 |
| `FeedbackPayload` | 轻量反馈载荷 |
| `ReviewProblemsPayload` | 复习题列表载荷 |

## 错误分类体系

内置可扩展的错误分类，各学科可通过 YAML 配置扩展或覆盖：

| 错误码 | 类别 | 说明 |
|--------|------|------|
| `CHAIN_RULE_MISUSE` | Rule Application Errors | 链式法则应用错误 |
| `SUBSTITUTION_MAPPING_MISMATCH` | Rule Application Errors | 变量代换前后不一致 |
| `SIGN_ARITHMETIC_ERROR` | Algebraic Manipulation Errors | 符号或算术化简错误 |
| `COEFFICIENT_OMISSION` | Algebraic Manipulation Errors | 遗漏系数或常数因子 |
| `FINAL_CALCULATION_ERROR` | Algebraic Manipulation Errors | 最终数值计算错误 |
| `DOMAIN_CONDITION_IGNORED` | Theorem/Condition Misuse | 忽略定义域或定理前提条件 |
| `OBJECT_CONFUSION_LIMIT_DERIVATIVE_INTEGRAL` | Conceptual Confusion | 混淆极限 / 导数 / 积分概念 |
| `UNSUPPORTED_JUMP` | Reasoning Quality Issues | 步骤缺乏充分推理依据 |
| `NOTATION_UNCLEAR` | Reasoning Quality Issues | 符号表达模糊或不清晰 |

## Provider 架构

```
LLMProvider (抽象基类)
├── MockProvider                — 确定性 mock 输出，用于调试与测试
└── OpenAICompatibleProvider    — OpenAI 兼容 API 调用
```

支持 4 种模型组：`reasoning` / `fast` / `ocr` / `baseline`，各节点可独立配置使用的模型组。验证节点额外支持 `verify` 模型组覆盖。

## 评估框架

### 评估命令

```bash
# Workflow 模式评估
python -m cli.evaluate --cases fixtures/eval_cases.json --provider mock --mode workflow_r1

# 保存评估结果
python -m cli.evaluate --cases fixtures/eval_cases.json --provider mock --mode workflow_r1 --output logs/eval/latest.json

# 真实模型评估
python -m cli.evaluate --cases fixtures/eval_cases.json --provider real --mode workflow_r1

# 基线对比（单提示，无工作流）
python -m cli.evaluate --cases fixtures/eval_cases.json --provider real --mode baseline_glm5
python -m cli.evaluate --cases fixtures/eval_cases.json --provider real --mode baseline_kimi
```

### 评估指标

| 指标 | 说明 |
|------|------|
| `avg_verification_accuracy` | 验证准确率 |
| `avg_diagnosis_hit` | 诊断命中率 |
| `avg_error_step_recall` | 错误步骤召回率 |
| `avg_taxonomy_category_hit` | 分类体系大类命中率 |
| `avg_first_error_hit` | 首个错误命中 |
| `avg_feedback_proxy` | 反馈质量代理 |
| `avg_review_relevance_proxy` | 复习题相关性代理 |
| `avg_low_conf_trigger_rate` | 低置信度触发率 |
| `avg_real_provider_failure_rate` | 真实模型失败率 |
| `avg_uncertainty_flags` | 不确定性标记数 |

## 设计原则

- **显式状态** — LangGraph 维护全局状态，所有中间结果可追踪
- **节点解耦** — 每个节点只负责一件事，便于单测和替换
- **提示词与逻辑分离** — 提示词在 `prompts/`，业务逻辑在 `nodes/`
- **领域知识分离** — 错误分类在 `taxonomy/`，学科配置在 `subjects/`
- **模型提供商可替换** — Provider 接口支持 mock / 真实 LLM 无缝切换
- **结构化输出优先** — 所有节点输出 Pydantic 模型，确保结构稳定
- **预算感知降级** — 全局预算池管控，预算不足时自动降级到轻量策略

## 说明

- 本项目为课程期末项目导向的工程原型
- 关注可解释与可验证，优先保证流程可信度
- 欢迎在保持模块边界清晰的前提下迭代扩展
- 首次启动 Web 服务自动创建管理员账户：`admin / admin123`
- 直接访问 `http://localhost:8000/#admin` 可进入管理员面板（需管理员账户登录）

## License

[Apache License 2.0](LICENSE)
