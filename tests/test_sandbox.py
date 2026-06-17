# -*- coding: utf-8 -*-
"""Python 计算沙箱：静态检查 + 子进程隔离 + 超时。

沙箱执行的是模型生成的代码，任何一条漏网都可能读写宿主文件或外联网络，
因此拒绝分支要比放行分支覆盖得更全。
"""

import time

import pytest

from src.sandbox import SandboxViolation, check_code, run_python_calc


# ---------------- 静态检查：拒绝 ----------------


@pytest.mark.parametrize(
    "code",
    [
        "import os",
        "import sys\nresult = 1",
        "from pathlib import Path",
        "__import__('os')",
        "result = ().__class__.__bases__",
        "result = x.__class__",
        "result = __builtins__",
    ],
)
def test_check_code_rejects(code):
    with pytest.raises((SandboxViolation, SyntaxError)):
        check_code(code)


def test_check_code_accepts_plain_arithmetic():
    check_code("result = sum([1, 2, 3]) * 2")


def test_check_code_rejects_syntax_error():
    with pytest.raises(SyntaxError):
        check_code("result = = 1")


# ---------------- 执行：正常路径 ----------------


@pytest.mark.parametrize(
    "code,expect",
    [
        ("result = 1 + 1", 2),
        ("result = sum([1, 2, 3])", 6),
        ("result = statistics.mean([1, 2, 3, 4])", 2.5),
        ("result = math.sqrt(16) + len([1, 2])", 6.0),
    ],
)
def test_calc_returns_value(code, expect):
    out = run_python_calc(code)
    assert out["ok"] is True
    assert out["result"] == expect


def test_calc_over_data_payload():
    out = run_python_calc(
        "result = sum(r[-1] for r in data['s1'])", data={"s1": [[1, 2.5], [3, 4.5]]}
    )
    assert out["ok"] is True
    assert out["result"] == 7.0


def test_calc_string_ops():
    out = run_python_calc("result = len(str(12345))")
    assert out["ok"] is True
    assert out["result"] == 5


# ---------------- 执行：拒绝与失败 ----------------


def test_calc_rejects_import():
    out = run_python_calc("import os\nresult = 1")
    assert out["ok"] is False
    assert "SandboxViolation" in out["error"]


def test_no_open_builtin():
    """命名空间里没有 open——IO 由白名单缺失兜底（不依赖静态检查）。"""
    out = run_python_calc("result = open('x.txt').read()")
    assert out["ok"] is False
    assert "NameError" in out["error"] or "SandboxViolation" in out["error"]


def test_timeout_kills_long_running():
    start = time.monotonic()
    out = run_python_calc("while True:\n    pass", timeout=1.0)
    assert out["ok"] is False
    assert "timeout" in out["error"].lower()
    assert time.monotonic() - start < 10


def test_runtime_error_reported():
    out = run_python_calc("result = 1 / 0")
    assert out["ok"] is False
    assert "ZeroDivision" in out["error"] or "error" in out["error"].lower()


# ---------------- 模块自测脚本可执行 ----------------


def test_module_selftest_passes():
    """sandbox.py 的 __main__ 自测覆盖 9 条用例，须全部 PASS。"""
    import subprocess
    import sys
    from pathlib import Path

    p = Path(__file__).resolve().parents[1] / "src" / "sandbox.py"
    r = subprocess.run(
        [sys.executable, str(p)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    assert "SANDBOX ALL PASS" in r.stdout
