"""Python 计算沙箱（升维 §3.4）：白名单命名空间 + 进程级超时 + 禁 import / IO / 内省。

- 执行隔离：独立子进程（超时可杀），AST 静态检查拒绝 import 与 `__` 开头名称（含属性访问）；
- 命名空间：白名单内置函数 + 预置模块（math / statistics / collections / itertools，直接可用、
  且不允许 import）；IO / 网络 / 内省接口均不在白名单内；
- 数据传入：上游数据经 `data` 注入（只读结构，经管道传递，不落盘）；
- 结果约定：被执行的代码须把最终结果赋给变量 `result`；
- 单次执行 ≤10s（子进程级超时）。
- 与 SQL 守卫并列——"两类代码执行、两套安全策略"。

测试：python -m src.sandbox
"""

from __future__ import annotations

import ast
import json
import pathlib
import subprocess
import sys

DEFAULT_TIMEOUT = 10.0

SAFE_BUILTINS = {
    "abs": abs,
    "min": min,
    "max": max,
    "sum": sum,
    "len": len,
    "round": round,
    "sorted": sorted,
    "range": range,
    "enumerate": enumerate,
    "zip": zip,
    "any": any,
    "all": all,
    "list": list,
    "dict": dict,
    "set": set,
    "tuple": tuple,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "divmod": divmod,
    "pow": pow,
    "reversed": reversed,
    "filter": filter,
    "map": map,
}


class SandboxViolation(Exception):
    """静态检查拒绝（import / 内省等）。"""


def check_code(code: str) -> None:
    """AST 静态检查：拒绝 import 语句与 `__` 开头名称 / 属性（禁内省）。"""
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise SandboxViolation(
                "禁止 import（模块已预置：math / statistics / collections / itertools）"
            )
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise SandboxViolation(f"禁止内省属性：.{node.attr}")
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise SandboxViolation(f"禁止访问特殊名称：{node.id}")


def exec_restricted(code: str, data) -> dict:
    """在受限命名空间中执行（仅由沙箱子进程调用）。"""
    import collections
    import itertools
    import math
    import statistics

    ns: dict = {
        "__builtins__": dict(SAFE_BUILTINS),
        "math": math,
        "statistics": statistics,
        "collections": collections,
        "itertools": itertools,
        "data": data,
        "result": None,
    }
    try:
        exec(compile(code, "<calc>", "exec"), ns, ns)
        r = ns.get("result")
        if not isinstance(r, (int, float, str, bool, type(None), list, tuple, dict)):
            r = repr(r)
        return {"ok": True, "result": r}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}


def _worker_main() -> None:
    """子进程入口：stdin 读 {code, data}，stdout 写结果 JSON（UTF-8 字节流）。"""
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    out = exec_restricted(payload.get("code", ""), payload.get("data"))
    sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8"))
    sys.stdout.buffer.flush()


def run_python_calc(code: str, data=None, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """在独立子进程中执行受限计算；返回 {ok, result} 或 {ok: False, error}。"""
    try:
        check_code(code)
    except SandboxViolation as e:
        return {"ok": False, "error": f"SandboxViolation: {e}"}
    except SyntaxError as e:
        return {"ok": False, "error": f"SyntaxError: {e}"}

    payload = json.dumps({"code": code, "data": data}, ensure_ascii=False).encode("utf-8")
    try:
        proc = subprocess.run(
            [sys.executable, str(pathlib.Path(__file__).resolve()), "--worker"],
            input=payload,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout: 执行超过 {timeout:.0f}s（已终止）"}
    if proc.returncode != 0:
        tail = (proc.stderr or b"").decode("utf-8", "ignore")[-200:]
        return {"ok": False, "error": f"子进程异常退出（rc={proc.returncode}）：{tail}"}
    try:
        return json.loads(proc.stdout.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": "结果解析失败（子进程无有效输出）"}


if __name__ == "__main__":
    if "--worker" in sys.argv:
        _worker_main()
        raise SystemExit(0)

    cases = [
        ("result = sum([1, 2, 3])", None, True, 10.0),
        ("result = statistics.mean([1, 2, 3, 4])", None, True, 10.0),
        ("result = math.sqrt(16) + len([1, 2])", None, True, 10.0),
        ("result = sum(r[-1] for r in data['s1'])", {"s1": [[1, 2.5], [3, 4.5]]}, True, 10.0),
        ("import os\nresult = 1", None, False, 10.0),  # import 被拒
        ("result = open('x.txt').read()", None, False, 10.0),  # IO 接口不存在
        ("result = __builtins__", None, False, 10.0),  # 内省被拒
        ("result = ().__class__.__bases__", None, False, 10.0),  # 内省被拒
        ("while True:\n    pass", None, False, 2.0),  # 超时被杀
    ]
    all_pass = True
    for i, (code, data, expect, tmo) in enumerate(cases, 1):
        out = run_python_calc(code, data, timeout=tmo)
        ok = out["ok"] == expect
        all_pass = all_pass and ok
        print(f"[{'PASS' if ok else 'FAIL'}] 用例{i} 期望通过={expect} → {str(out)[:96]}")
    print("SANDBOX ALL PASS" if all_pass else "SANDBOX SOME FAILED")
