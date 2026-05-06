"""服务化外壳（演示级）：把全链路能力暴露为 HTTP API。

- POST /v1/ask   {"question": "...", "route": "single" | "multi"}
    · single：单步全链路（理解 → 链接 → 生成 → 守卫 → 执行 → 解读；含澄清 / 重试 / 拒答）
    · multi ：外层多步编排（plan → execute_step → synthesize；三技能）
    返回：{ok, route, answer, sql, rows_n / steps, path, trace_file, ms}
- GET  /healthz  健康检查（LLM 端点 / 库文件 / 并发配置）

工程要点（生产化差距与迁移路径见 docs/生产化路线.md）：
- 并发闸：全局 Semaphore 控制同时在跑请求（默认 4；环境变量 SERVICE_MAX_CONCURRENCY）
- 可选鉴权：设置 SERVICE_API_KEY 后，请求需携带 X-API-Key
- 同步实现经线程池执行（FastAPI 对 def 端点自动调度），不阻塞事件循环

运行：
    uvicorn service:app --host 0.0.0.0 --port 8000
    curl -X POST http://127.0.0.1:8000/v1/ask -H "Content-Type: application/json" \\
         -d '{"question": "2018 年各州订单量排名前五？"}'
"""

from __future__ import annotations

import os
import pathlib
import sys
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from fastapi import FastAPI, Header, HTTPException  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from src.graph import run as run_single  # noqa: E402
from src.llm import LLM  # noqa: E402
from src.orchestrator import run_task  # noqa: E402
from src.pipeline import DB_PATH  # noqa: E402

MAX_CONCURRENCY = int(os.getenv("SERVICE_MAX_CONCURRENCY", "4"))
SEM = threading.Semaphore(MAX_CONCURRENCY)

app = FastAPI(title="data-agent service", version="0.1.0")


class AskRequest(BaseModel):
    question: str
    route: str = "single"  # single | multi


def _check_key(x_api_key: str | None) -> None:
    want = os.getenv("SERVICE_API_KEY", "")
    if want and x_api_key != want:
        raise HTTPException(status_code=401, detail="无效的 X-API-Key")


def _make_llm() -> LLM:
    if not os.getenv("LLM_API_KEY"):
        raise HTTPException(
            status_code=503, detail="未配置 LLM_API_KEY（服务需要真实端点；见 .env.example）"
        )
    return LLM()


@app.get("/healthz")
def healthz() -> dict:
    return {
        "ok": True,
        "llm": {
            "configured": bool(os.getenv("LLM_API_KEY")),
            "base_url": os.getenv("LLM_BASE_URL", ""),
            "model": os.getenv("LLM_MODEL", ""),
        },
        "db": {"path": str(DB_PATH), "exists": DB_PATH.exists()},
        "concurrency": MAX_CONCURRENCY,
        "auth": "X-API-Key" if os.getenv("SERVICE_API_KEY") else "off",
    }


@app.post("/v1/ask")
def ask(req: AskRequest, x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> dict:
    _check_key(x_api_key)
    q = (req.question or "").strip()
    if not q:
        raise HTTPException(status_code=422, detail="question 不能为空")
    if req.route not in ("single", "multi"):
        raise HTTPException(status_code=422, detail="route 仅支持 single / multi")
    llm = _make_llm()

    t0 = time.perf_counter()
    with SEM:
        if req.route == "multi":
            result = run_task(q, mode="eval", llm=llm)
            fin = result["trace"]["final"]
            payload = {
                "sql": "",
                "rows_n": None,
                "steps": len(result["trace"].get("steps", [])),
            }
        else:
            result = run_single(q, mode="eval", llm=llm)
            fin = result["trace"]["final"]
            payload = {
                "sql": fin.get("sql", ""),
                "rows_n": fin.get("rows_n") or 0,
                "steps": 1,
            }
    ms = round((time.perf_counter() - t0) * 1000, 1)
    answer = fin.get("answer", "")
    return {
        "ok": bool(answer),
        "route": req.route,
        "answer": answer,
        **payload,
        "path": fin.get("path", ""),
        "trace_file": result["trace_file"],
        "ms": ms,
    }
