from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import subprocess
import sys


class ModelGroup(str, Enum):
    REASONING = "reasoning"
    FAST = "fast"
    OCR = "ocr"
    BASELINE = "baseline"


DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_MAX_RETRIES = 1
DEFAULT_SYMPY_ENABLED = True
DEFAULT_SYMPY_TIMEOUT = 3.0
DEFAULT_DUAL_MODEL_ENABLED = False
DEFAULT_PYTHON_SANDBOX_TIMEOUT = 10.0
DEFAULT_LOAD_LEGACY_TOOLS = False
DEFAULT_VERIFY_MODEL_GROUP = "fast"
DEFAULT_REFERENCE_MAX_TOOL_ROUNDS = 1
DEFAULT_AGENT_REQUEST_TIMEOUT = 45
DEFAULT_AGENT_MAX_DURATION = 90
DEFAULT_HINT_MAX_CHARS = 1200
DEFAULT_SIMPLE_FASTPATH_ENABLED = True
DEFAULT_DETERMINISTIC_VERIFY_ENABLED = True
DEFAULT_TOOL_RESULT_MAX_CHARS = 200
DEFAULT_NODE_TIMING_ENABLED = True
DEFAULT_PARALLEL_REVIEW_ENABLED = True
DEFAULT_DEPTH = "standard"


@dataclass(frozen=True)
class ProviderSettings:
    provider_type: str = "mock"
    subject_id: str = "calculus"
    api_key: str = ""
    base_url: str = ""
    model: str = ""  # backward-compatible alias to reasoning_model_name
    reasoning_model_name: str = "qwen/qwen3.6-plus"
    fast_model_name: str = "deepseek/deepseek-v3.2"
    ocr_model_name: str = "qwen/qwen3.6-plus"
    baseline_glm5_model_name: str = "qwen/qwen3-30b-a3b-instruct-2507"
    baseline_kimi_model_name: str = "qwen/qwen3-30b-a3b-instruct-2507"
    detection_model_name: str = "qwen/qwen3-30b-a3b-instruct-2507"
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES
    allow_mock_fallback: bool = True
    verify_model_group: str = DEFAULT_VERIFY_MODEL_GROUP
    verify_model_name: str = ""

    def resolve_model_name(self, model_group: str, baseline_name: str | None = None) -> str:
        group = model_group.strip().lower()
        if group == ModelGroup.REASONING.value:
            return self.reasoning_model_name
        if group == ModelGroup.FAST.value:
            return self.fast_model_name
        if group == ModelGroup.OCR.value:
            return self.ocr_model_name
        if group == ModelGroup.BASELINE.value:
            if baseline_name == "glm5":
                return self.baseline_glm5_model_name
            if baseline_name == "kimi":
                return self.baseline_kimi_model_name
            raise ValueError(f"Unsupported baseline name: {baseline_name}")
        raise ValueError(f"Unsupported model group: {model_group}")


def _load_env_file(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists() or not path.is_file():
        return data

    text = path.read_text(encoding="utf-8-sig")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def _find_key_env(start_dir: Path) -> Path | None:
    candidates = [
        start_dir / "key.env",
        start_dir.parent / "key.env",
        start_dir.parent.parent / "key.env",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def load_provider_settings() -> ProviderSettings:
    cwd = Path.cwd()
    env_file = _find_key_env(cwd)
    env_data = _load_env_file(env_file) if env_file else {}

    provider_type = os.environ.get("STEM_TUTOR_PROVIDER", env_data.get("STEM_TUTOR_PROVIDER", "mock")).strip().lower()
    subject_id = os.environ.get("STEM_TUTOR_SUBJECT", env_data.get("STEM_TUTOR_SUBJECT", "calculus")).strip().lower()
    api_key = os.environ.get("PARATERA_API_KEY", env_data.get("PARATERA_API_KEY", "")).strip()
    base_url = os.environ.get("PARATERA_URL", env_data.get("PARATERA_URL", "")).strip().rstrip("/")
    default_reasoning = os.environ.get("PARATERA_MODEL", env_data.get("PARATERA_MODEL", "qwen/qwen3.6-plus")).strip()
    reasoning_model_name = os.environ.get("STEM_TUTOR_REASONING_MODEL", env_data.get("STEM_TUTOR_REASONING_MODEL", default_reasoning)).strip()
    fast_model_name = os.environ.get("STEM_TUTOR_FAST_MODEL", env_data.get("STEM_TUTOR_FAST_MODEL", "deepseek/deepseek-v3.2")).strip()
    ocr_model_name = os.environ.get("STEM_TUTOR_OCR_MODEL", env_data.get("STEM_TUTOR_OCR_MODEL", "qwen/qwen3.6-plus")).strip()
    baseline_glm5_model_name = os.environ.get(
        "STEM_TUTOR_BASELINE_GLM5_MODEL",
        env_data.get("STEM_TUTOR_BASELINE_GLM5_MODEL", "qwen/qwen3-30b-a3b-instruct-2507"),
    ).strip()
    baseline_kimi_model_name = os.environ.get(
        "STEM_TUTOR_BASELINE_KIMI_MODEL",
        env_data.get("STEM_TUTOR_BASELINE_KIMI_MODEL", "qwen/qwen3-30b-a3b-instruct-2507"),
    ).strip()
    detection_model_name = os.environ.get(
        "STEM_TUTOR_DETECTION_MODEL",
        env_data.get("STEM_TUTOR_DETECTION_MODEL", "qwen/qwen3-30b-a3b-instruct-2507"),
    ).strip()

    timeout_seconds = int(os.environ.get("STEM_TUTOR_TIMEOUT", env_data.get("STEM_TUTOR_TIMEOUT", str(DEFAULT_TIMEOUT_SECONDS))))
    max_retries = int(os.environ.get("STEM_TUTOR_MAX_RETRIES", env_data.get("STEM_TUTOR_MAX_RETRIES", str(DEFAULT_MAX_RETRIES))))
    allow_mock_fallback = os.environ.get("STEM_TUTOR_ALLOW_MOCK_FALLBACK", env_data.get("STEM_TUTOR_ALLOW_MOCK_FALLBACK", "true")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    verify_model_group = os.environ.get("STEM_TUTOR_VERIFY_MODEL_GROUP", env_data.get("STEM_TUTOR_VERIFY_MODEL_GROUP", DEFAULT_VERIFY_MODEL_GROUP)).strip().lower()
    verify_model_name = os.environ.get("STEM_TUTOR_VERIFY_MODEL", env_data.get("STEM_TUTOR_VERIFY_MODEL", "")).strip()

    return ProviderSettings(
        provider_type=provider_type,
        subject_id=subject_id,
        api_key=api_key,
        base_url=base_url,
        model=reasoning_model_name,
        reasoning_model_name=reasoning_model_name,
        fast_model_name=fast_model_name,
        ocr_model_name=ocr_model_name,
        baseline_glm5_model_name=baseline_glm5_model_name,
        baseline_kimi_model_name=baseline_kimi_model_name,
        detection_model_name=detection_model_name,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        allow_mock_fallback=allow_mock_fallback,
        verify_model_group=verify_model_group,
        verify_model_name=verify_model_name,
    )


def is_sympy_enabled() -> bool:
    val = os.environ.get("STEM_TUTOR_SYMPY_ENABLED", "").strip().lower()
    if val:
        return val in {"1", "true", "yes", "on"}
    env_data = _load_env_file(_find_key_env(Path.cwd()) or Path(""))
    val2 = env_data.get("STEM_TUTOR_SYMPY_ENABLED", "").strip().lower()
    if val2:
        return val2 in {"1", "true", "yes", "on"}
    return DEFAULT_SYMPY_ENABLED


def is_tool_calling_enabled() -> bool:
    val = os.environ.get("STEM_TUTOR_TOOL_CALLING", "").strip().lower()
    if val:
        return val in {"1", "true", "yes", "on"}
    env_data = _load_env_file(_find_key_env(Path.cwd()) or Path(""))
    val2 = env_data.get("STEM_TUTOR_TOOL_CALLING", "").strip().lower()
    if val2:
        return val2 in {"1", "true", "yes", "on"}
    return False


def sympy_timeout() -> float:
    val = os.environ.get("STEM_TUTOR_SYMPY_TIMEOUT", "").strip()
    if val:
        try:
            return float(val)
        except ValueError:
            pass
    env_data = _load_env_file(_find_key_env(Path.cwd()) or Path(""))
    val2 = env_data.get("STEM_TUTOR_SYMPY_TIMEOUT", "").strip()
    if val2:
        try:
            return float(val2)
        except ValueError:
            pass
    return DEFAULT_SYMPY_TIMEOUT


def is_dual_model_enabled() -> bool:
    val = os.environ.get("STEM_TUTOR_DUAL_MODEL", "").strip().lower()
    if val:
        return val in {"1", "true", "yes", "on"}
    env_data = _load_env_file(_find_key_env(Path.cwd()) or Path(""))
    val2 = env_data.get("STEM_TUTOR_DUAL_MODEL", "").strip().lower()
    if val2:
        return val2 in {"1", "true", "yes", "on"}
    return DEFAULT_DUAL_MODEL_ENABLED


def python_sandbox_timeout() -> float:
    val = os.environ.get("STEM_TUTOR_PYTHON_TIMEOUT", "").strip()
    if val:
        try:
            return float(val)
        except ValueError:
            pass
    env_data = _load_env_file(_find_key_env(Path.cwd()) or Path(""))
    val2 = env_data.get("STEM_TUTOR_PYTHON_TIMEOUT", "").strip()
    if val2:
        try:
            return float(val2)
        except ValueError:
            pass
    return DEFAULT_PYTHON_SANDBOX_TIMEOUT


def is_legacy_tools_enabled() -> bool:
    val = os.environ.get("STEM_TUTOR_LOAD_LEGACY_TOOLS", "").strip().lower()
    if val:
        return val in {"1", "true", "yes", "on"}
    env_data = _load_env_file(_find_key_env(Path.cwd()) or Path(""))
    val2 = env_data.get("STEM_TUTOR_LOAD_LEGACY_TOOLS", "").strip().lower()
    if val2:
        return val2 in {"1", "true", "yes", "on"}
    return DEFAULT_LOAD_LEGACY_TOOLS


def reference_max_tool_rounds() -> int:
    val = os.environ.get("STEM_TUTOR_REFERENCE_MAX_TOOL_ROUNDS", "").strip()
    if val:
        try:
            return max(1, int(val))
        except ValueError:
            pass
    env_data = _load_env_file(_find_key_env(Path.cwd()) or Path(""))
    val2 = env_data.get("STEM_TUTOR_REFERENCE_MAX_TOOL_ROUNDS", "").strip()
    if val2:
        try:
            return max(1, int(val2))
        except ValueError:
            pass
    return DEFAULT_REFERENCE_MAX_TOOL_ROUNDS


def agent_request_timeout() -> int:
    val = os.environ.get("STEM_TUTOR_AGENT_REQUEST_TIMEOUT", "").strip()
    if val:
        try:
            return max(5, int(val))
        except ValueError:
            pass
    return DEFAULT_AGENT_REQUEST_TIMEOUT


def agent_max_duration() -> int:
    val = os.environ.get("STEM_TUTOR_AGENT_MAX_DURATION", "").strip()
    if val:
        try:
            return max(10, int(val))
        except ValueError:
            pass
    return DEFAULT_AGENT_MAX_DURATION


def hint_max_chars() -> int:
    val = os.environ.get("STEM_TUTOR_HINT_MAX_CHARS", "").strip()
    if val:
        try:
            return max(200, int(val))
        except ValueError:
            pass
    return DEFAULT_HINT_MAX_CHARS


def include_failed_hints() -> bool:
    val = os.environ.get("STEM_TUTOR_INCLUDE_FAILED_HINTS", "").strip().lower()
    if val:
        return val in {"1", "true", "yes", "on"}
    return False


def is_simple_fastpath_enabled() -> bool:
    val = os.environ.get("STEM_TUTOR_SIMPLE_FASTPATH", "").strip().lower()
    if val:
        return val in {"1", "true", "yes", "on"}
    env_data = _load_env_file(_find_key_env(Path.cwd()) or Path(""))
    val2 = env_data.get("STEM_TUTOR_SIMPLE_FASTPATH", "").strip().lower()
    if val2:
        return val2 in {"1", "true", "yes", "on"}
    return DEFAULT_SIMPLE_FASTPATH_ENABLED


def is_deterministic_verify_enabled() -> bool:
    val = os.environ.get("STEM_TUTOR_DETERMINISTIC_VERIFY", "").strip().lower()
    if val:
        return val in {"1", "true", "yes", "on"}
    env_data = _load_env_file(_find_key_env(Path.cwd()) or Path(""))
    val2 = env_data.get("STEM_TUTOR_DETERMINISTIC_VERIFY", "").strip().lower()
    if val2:
        return val2 in {"1", "true", "yes", "on"}
    return DEFAULT_DETERMINISTIC_VERIFY_ENABLED


def tool_result_max_chars() -> int:
    val = os.environ.get("STEM_TUTOR_TOOL_RESULT_MAX_CHARS", "").strip()
    if val:
        try:
            return max(50, int(val))
        except ValueError:
            pass
    env_data = _load_env_file(_find_key_env(Path.cwd()) or Path(""))
    val2 = env_data.get("STEM_TUTOR_TOOL_RESULT_MAX_CHARS", "").strip()
    if val2:
        try:
            return max(50, int(val2))
        except ValueError:
            pass
    return DEFAULT_TOOL_RESULT_MAX_CHARS


def is_node_timing_enabled() -> bool:
    val = os.environ.get("STEM_TUTOR_NODE_TIMING", "").strip().lower()
    if val:
        return val in {"1", "true", "yes", "on"}
    env_data = _load_env_file(_find_key_env(Path.cwd()) or Path(""))
    val2 = env_data.get("STEM_TUTOR_NODE_TIMING", "").strip().lower()
    if val2:
        return val2 in {"1", "true", "yes", "on"}
    return DEFAULT_NODE_TIMING_ENABLED


def is_parallel_review_enabled() -> bool:
    val = os.environ.get("STEM_TUTOR_PARALLEL_REVIEW", "").strip().lower()
    if val:
        return val in {"1", "true", "yes", "on"}
    env_data = _load_env_file(_find_key_env(Path.cwd()) or Path(""))
    val2 = env_data.get("STEM_TUTOR_PARALLEL_REVIEW", "").strip().lower()
    if val2:
        return val2 in {"1", "true", "yes", "on"}
    return DEFAULT_PARALLEL_REVIEW_ENABLED


def load_depth() -> str:
    val = os.environ.get("STEM_TUTOR_DEPTH", "").strip().lower()
    if val in ("quick", "standard", "thorough"):
        return val
    env_data = _load_env_file(_find_key_env(Path.cwd()) or Path(""))
    val2 = env_data.get("STEM_TUTOR_DEPTH", "").strip().lower()
    if val2 in ("quick", "standard", "thorough"):
        return val2
    return DEFAULT_DEPTH


def python_executable() -> str:
    """获取用于执行 Python 代码的解释器路径。

    优先级：
    1. 环境变量 STEM_TUTOR_PYTHON_EXECUTABLE
    2. 自动检测 conda LLM 环境
    3. 当前 Python 解释器 (sys.executable)
    """
    env_path = os.environ.get("STEM_TUTOR_PYTHON_EXECUTABLE", "").strip()
    if env_path:
        p = Path(env_path)
        if p.exists() and p.is_file():
            return str(p)
        import logging
        logging.getLogger(__name__).warning(
            "STEM_TUTOR_PYTHON_EXECUTABLE points to non-existent path: %s", env_path
        )

    try:
        result = subprocess.run(
            ["conda", "info", "--base"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            conda_base = result.stdout.strip()
            if conda_base:
                candidate = Path(conda_base) / "envs" / "LLM" / "python.exe"
                if candidate.exists() and candidate.is_file():
                    return str(candidate)
    except Exception:
        pass

    import logging
    logging.getLogger(__name__).warning(
        "Falling back to sys.executable (%s). sympy/numpy/scipy may not be available. "
        "Set STEM_TUTOR_PYTHON_EXECUTABLE to the correct Python path.",
        sys.executable,
    )
    return sys.executable
