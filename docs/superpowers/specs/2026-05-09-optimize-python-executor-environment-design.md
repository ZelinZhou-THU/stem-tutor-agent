# 设计文档：优化 Python 代码执行器环境配置

**日期**: 2026-05-09
**作者**: OpenCode
**状态**: 待审查

## 1. 问题概述

### 1.1 问题描述
在 `generate_reference_solution` 节点中，`execute_python` 工具被调用了 3 次，其中 2 次失败：

1. **第一次失败**: `ImportError: No module named 'scipy'` - scipy 未安装
2. **第二次失败**: `RuntimeError: ExitCode: 1` - numpy.trapz 调用失败
3. **第三次成功**: 使用简单的矩形法则

这些错误浪费了大量时间（总计 159 秒），降低了系统效率。

### 1.2 根本原因
`execute_python` 工具使用 `sys.executable` 来执行代码，这取决于服务启动时使用的 Python 环境。当服务在错误的 Python 环境中启动时（例如使用系统 Python 而非 LLM conda 环境），scipy 等必需库不可用。

### 1.3 影响
- **性能**: 工具调用失败导致重试，增加延迟
- **可靠性**: 参考解答质量下降（日志显示 quality=degraded）
- **用户体验**: 分析时间延长，可能超时

## 2. 设计目标

### 2.1 核心目标
确保 `execute_python` 工具始终使用 LLM conda 环境的 Python 解释器，保证 sympy、numpy、scipy 都可用。

### 2.2 约束条件
- 项目仅在 Windows 平台运行
- 使用 conda LLM 环境
- PowerShell 不支持 `conda activate`，不能依赖激活环境

### 2.3 成功标准
- `execute_python` 工具调用成功率接近 100%
- 工具执行时间稳定，不因环境问题重试
- 配置简单，开发者和运维人员易于理解

## 3. 解决方案：混合方案（环境变量 + 自动检测）

### 3.1 方案概述
采用多层优先级的 Python 解释器选择策略：

1. **优先级 1**: 环境变量 `STEM_TUTOR_PYTHON_EXECUTABLE`
2. **优先级 2**: 自动检测 conda LLM 环境
3. **优先级 3**: 回退到 `sys.executable`（带警告）

### 3.2 架构设计

```
┌─────────────────────────────────────────┐
│      Settings 配置层                     │
│  python_executable() 函数                │
│  - 环境变量读取                          │
│  - conda 自动检测                        │
│  - 回退机制                              │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│      execute_python 工具层               │
│  - 使用 python_executable()              │
│  - 环境验证（可选）                      │
│  - 改进错误提示                          │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│      子进程执行层                         │
│  - subprocess.Popen                      │
│  - 捕获 stdout/stderr                    │
│  - 错误处理                              │
└─────────────────────────────────────────┘
```

## 4. 组件设计

### 4.1 组件 1: `settings.py` - 新增函数

#### 函数签名
```python
def python_executable() -> str:
    """
    获取用于执行 Python 代码的解释器路径。

    优先级：
    1. 环境变量 STEM_TUTOR_PYTHON_EXECUTABLE
    2. 自动检测 conda LLM 环境
    3. 当前 Python 解释器 (sys.executable)

    Returns:
        Python 解释器的绝对路径

    Raises:
        RuntimeError: 当所有方法都无法找到可执行的 Python 解释器时
    """
```

#### 实现逻辑
```python
def python_executable() -> str:
    # 1. 检查环境变量
    env_path = os.environ.get("STEM_TUTOR_PYTHON_EXECUTABLE", "").strip()
    if env_path:
        if Path(env_path).exists() and Path(env_path).is_file():
            logger.info(f"Using Python from environment variable: {env_path}")
            return env_path
        else:
            logger.warning(f"STEM_TUTOR_PYTHON_EXECUTABLE points to non-existent path: {env_path}")

    # 2. 自动检测 conda LLM 环境
    try:
        # 检测 conda 根目录
        result = subprocess.run(
            ["conda", "info", "--root"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            conda_root = result.stdout.strip()
            llm_python = Path(conda_root) / "envs" / "LLM" / "python.exe"
            if llm_python.exists() and llm_python.is_file():
                logger.info(f"Auto-detected conda LLM environment: {llm_python}")
                return str(llm_python)
    except Exception as e:
        logger.debug(f"Failed to detect conda LLM environment: {e}")

    # 3. 回退到当前 Python
    logger.warning(
        f"Using current Python interpreter: {sys.executable}. "
        "This may not have required libraries (sympy, numpy, scipy). "
        "Consider setting STEM_TUTOR_PYTHON_EXECUTABLE environment variable."
    )
    return sys.executable
```

#### 默认配置
```python
DEFAULT_PYTHON_EXECUTABLE = None  # 使用自动检测
```

### 4.2 组件 2: `execute_python.py` - 修改现有函数

#### 修改点 1: 使用 `python_executable()`
```python
@tool
def execute_python(code: str) -> str:
    blacklist_error = _check_blacklist(code)
    if blacklist_error:
        return blacklist_error

    timeout = _get_timeout()
    python_path = python_executable()  # 替换原来的 sys.executable

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"

    proc = subprocess.Popen(
        [python_path, "-u", "-c", code],  # 使用 python_path
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    # ... 其余代码保持不变
```

#### 修改点 2: 改进错误提示（可选）
在 RuntimeError 的错误信息中添加 Python 路径：
```python
return (
    f"[Error Type: RuntimeError | ExitCode: {proc.returncode} | "
    f"Python: {python_path} | "
    f"Detail: {clean} | "
    f"Suggestion: review code logic and error trace above]"
)
```

### 4.3 组件 3: 测试用例

#### 测试文件
`tests/test_execute_python_environment.py`

#### 测试用例
1. **test_environment_variable_priority**
   - 设置 `STEM_TUTOR_PYTHON_EXECUTABLE` 环境变量
   - 验证 `python_executable()` 返回指定路径
   - 清理环境变量

2. **test_conda_auto_detection**
   - Mock `subprocess.run` 返回 conda 根目录
   - 验证正确构建 LLM 环境路径
   - 验证路径存在性检查

3. **test_fallback_to_sys_executable**
   - Mock conda 检测失败
   - 验证回退到 `sys.executable`
   - 验证警告日志被记录

4. **test_library_availability**
   - 使用 `python_executable()` 获取路径
   - 执行测试代码导入 sympy, numpy, scipy
   - 验证所有库都可用

5. **test_execute_python_integration**
   - 调用 `execute_python` 工具
   - 验证成功执行数学计算
   - 验证输出正确

## 5. 数据流

```
服务启动
    ↓
execute_python 被调用
    ↓
python_executable() 获取解释器路径
    ↓
优先级检查：
  ├─ 环境变量 STEM_TUTOR_PYTHON_EXECUTABLE? → 使用
  ├─ conda 检测成功? → 使用 conda LLM 环境
  └─ 回退到 sys.executable → 记录警告
    ↓
subprocess.Popen 启动子进程
    ↓
执行代码并捕获输出
    ↓
返回结果
```

## 6. 错误处理

### 6.1 场景 1: 环境变量指向的 Python 不存在
- **行为**: 跳过环境变量，继续检测 conda
- **日志**: `WARNING: STEM_TUTOR_PYTHON_EXECUTABLE points to non-existent path: {path}`
- **建议**: 检查环境变量配置

### 6.2 场景 2: conda 检测失败
- **行为**: 静默回退到 `sys.executable`
- **日志**: `WARNING: Failed to detect conda LLM environment: {error}`
- **日志**: `WARNING: Using current Python interpreter: {sys.executable}. This may not have required libraries...`
- **建议**: 安装 conda 或设置环境变量

### 6.3 场景 3: Python 环境缺少必需库
- **行为**: 工具调用时失败，返回 ImportError
- **错误信息**: 包含缺少的库名称和建议
- **建议**: 使用正确的 conda LLM 环境或安装缺失的库

### 6.4 场景 4: 子进程执行失败
- **行为**: 保持现有的错误处理逻辑
- **增强**: 在错误信息中包含使用的 Python 路径
- **建议**: 便于调试环境问题

## 7. 配置指南

### 7.1 开发环境（推荐）
使用自动检测，无需额外配置：
```powershell
# 在 PowerShell 中直接启动服务
python -m uvicorn web.app:app --reload
```

### 7.2 生产环境（推荐）
通过环境变量明确指定 Python 路径：

**Windows (PowerShell)**:
```powershell
$env:STEM_TUTOR_PYTHON_EXECUTABLE = "D:\Applications\Anaconda3\envs\LLM\python.exe"
python -m uvicorn web.app:app
```

**Windows (命令提示符)**:
```cmd
set STEM_TUTOR_PYTHON_EXECUTABLE=D:\Applications\Anaconda3\envs\LLM\python.exe
python -m uvicorn web.app:app
```

**环境变量文件 (key.env)**:
```
STEM_TUTOR_PYTHON_EXECUTABLE=D:\Applications\Anaconda3\envs\LLM\python.exe
```

### 7.3 验证配置
```python
# 在 Python 中验证
from stem_tutor.settings import python_executable
print(python_executable())
```

## 8. 迁移计划

### 8.1 开发阶段
1. 实现 `python_executable()` 函数
2. 修改 `execute_python` 工具
3. 编写单元测试
4. 在开发环境测试

### 8.2 测试阶段
1. 运行完整测试套件
2. 验证工具调用成功率
3. 检查日志输出
4. 性能基准测试

### 8.3 部署阶段
1. 更新部署文档
2. 配置生产环境变量
3. 灰度发布
4. 监控工具调用成功率

## 9. 风险与缓解

### 9.1 风险 1: conda 检测在不同机器上失败
- **影响**: 回退到错误的 Python 环境
- **缓解**: 通过环境变量明确指定生产环境的 Python 路径

### 9.2 风险 2: 环境变量配置错误
- **影响**: 工具无法启动
- **缓解**: 启动时验证 Python 路径，记录清晰错误信息

### 9.3 风险 3: Windows 路径格式问题
- **影响**: 路径解析失败
- **缓解**: 使用 `pathlib.Path` 处理跨平台路径

## 10. 成功指标

### 10.1 功能指标
- `execute_python` 工具调用成功率 > 99%
- scipy/numpy/sympy ImportError 错误 = 0

### 10.2 性能指标
- 平均工具执行时间 < 5 秒
- 工具重试次数 = 0（首次调用即成功）

### 10.3 质量指标
- 参考解答质量从 "degraded" 提升到 "good"
- 单元测试覆盖率 > 90%

## 11. 未来改进

### 11.1 短期改进
- 添加环境健康检查端点
- 实现环境自动修复机制

### 11.2 长期改进
- 支持多环境配置（开发/测试/生产）
- 实现环境版本管理
- 添加性能监控和告警

## 12. 附录

### 12.1 相关文件
- `stem_tutor/settings.py` - 配置函数
- `stem_tutor/tools/execute_python.py` - 工具实现
- `tests/test_execute_python_environment.py` - 测试文件
- `docs/configuration.md` - 配置文档（待创建）

### 12.2 参考资料
- [Conda Documentation](https://docs.conda.io/)
- [Python subprocess module](https://docs.python.org/3/library/subprocess.html)
- [LangChain Tools](https://python.langchain.com/docs/modules/tools/)

---

**文档版本**: 1.0
**最后更新**: 2026-05-09
