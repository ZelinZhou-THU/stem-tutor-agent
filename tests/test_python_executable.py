import os
import sys
from unittest import mock

import pytest


def test_env_variable_takes_priority():
    """环境变量 STEM_TUTOR_PYTHON_EXECUTABLE 优先级最高。"""
    fake_path = r"D:\Applications\Anaconda3\envs\LLM\python.exe"
    with mock.patch.dict(os.environ, {"STEM_TUTOR_PYTHON_EXECUTABLE": fake_path}):
        with mock.patch("pathlib.Path.exists", return_value=True):
            with mock.patch("pathlib.Path.is_file", return_value=True):
                from stem_tutor.settings import python_executable
                assert python_executable() == fake_path


def test_env_variable_nonexistent_path_falls_through():
    """环境变量指向不存在的路径时，应继续尝试 conda 检测。"""
    with mock.patch.dict(os.environ, {"STEM_TUTOR_PYTHON_EXECUTABLE": r"C:\nonexistent\python.exe"}):
        with mock.patch("pathlib.Path.exists", return_value=False):
            with mock.patch("subprocess.run") as mock_run:
                mock_run.return_value = mock.Mock(returncode=1, stdout="", stderr="")
                from stem_tutor.settings import python_executable
                result = python_executable()
                assert result == sys.executable


def test_conda_auto_detection():
    """conda 自动检测应返回 LLM 环境的 python.exe 路径。"""
    with mock.patch.dict(os.environ, {}, clear=True):
        env = os.environ.copy()
        env.pop("STEM_TUTOR_PYTHON_EXECUTABLE", None)
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("subprocess.run") as mock_run:
                mock_run.return_value = mock.Mock(
                    returncode=0,
                    stdout=r"D:\Applications\Anaconda3",
                    stderr="",
                )
                with mock.patch("pathlib.Path.exists", return_value=True):
                    with mock.patch("pathlib.Path.is_file", return_value=True):
                        from stem_tutor.settings import python_executable
                        result = python_executable()
                        assert result.endswith(r"envs\LLM\python.exe")
                        assert r"D:\Applications\Anaconda3" in result


def test_fallback_to_sys_executable():
    """当环境变量和 conda 检测都失败时，回退到 sys.executable。"""
    with mock.patch.dict(os.environ, {}, clear=True):
        env = os.environ.copy()
        env.pop("STEM_TUTOR_PYTHON_EXECUTABLE", None)
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("subprocess.run", side_effect=Exception("no conda")):
                from stem_tutor.settings import python_executable
                assert python_executable() == sys.executable


def test_execute_python_uses_correct_environment():
    """execute_python 工具应能成功导入 sympy, numpy, scipy。"""
    from stem_tutor.tools.execute_python import execute_python
    result = execute_python.invoke({"code": "import sympy, numpy, scipy; print('OK')"})
    assert "OK" in result
