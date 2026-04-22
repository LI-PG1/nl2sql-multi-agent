"""技能层（升维 §3.3）：query_data / compute / search_doc。

- query_data：现有 NL2SQL Pipeline 整体包装（复用 src/graph.run，内部零改动）；
- compute：LLM 生成 Python → 沙箱执行（src/sandbox），失败回喂 ≤1 次重生成；
- search_doc：口径文档检索（data/index/doc_index，由 scripts/build_doc_index.py 构建）。
"""

from __future__ import annotations

import re

from . import sandbox
from .config import load_settings
from .embed import get_embedder
from .graph import run as run_nl2sql
from .index_store import VectorIndex
from .linking import INDEX_DIR
from .prompts import SYSTEM, load_prompt, render


def _chat(llm, system: str, user: str, max_tokens: int | None = None) -> str:
    """兼容 Mock 客户端与真实客户端（带生成上限纪律）。"""
    try:
        return (
            llm.chat(system, user, max_tokens=max_tokens) if max_tokens else llm.chat(system, user)
        )
    except TypeError:
        return llm.chat(system, user)


# ================================================================ query_data
def query_data(question: str, llm, db_path) -> dict:
    """现有 NL2SQL Pipeline 整体包装——内部零改动（含澄清 / 守卫 / 执行反馈重试 / 解读）。"""
    r = run_nl2sql(question, mode="eval", llm=llm, db_path=db_path)
    fin = r["final"]
    rows = fin.get("rows") or []
    return {
        "path": fin.get("path") or "",
        "sql": fin.get("sql") or "",
        "rows_n": len(rows),
        "rows_preview": "; ".join(str(x) for x in rows[:5]),
        "answer": (fin.get("answer") or "").strip(),
        "rows": [list(x) for x in rows],
        "trace_file": r["trace_file"],
    }


# ================================================================ compute
def _extract_code(raw: str) -> str:
    """提取代码：优先围栏块；无闭合围栏（截断）时去掉残留围栏行兜底。"""
    text = raw or ""
    m = re.search(r"```(?:python|py)?\s*\n?(.*?)```", text, re.S | re.I)
    if m:
        return m.group(1).strip()
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith("```")]
    return "\n".join(lines).strip()


def _data_desc(upstream: dict, head: int = 30) -> str:
    if not upstream:
        return "（无上游数据）"
    lines = []
    for sid, rows in upstream.items():
        if rows:
            lines.append(
                f"- data['{sid}']：{len(rows)} 行，前 {min(head, len(rows))} 行：{rows[:head]}"
            )
        else:
            lines.append(f"- data['{sid}']：0 行（上游结果为空）")
    return "\n".join(lines)


def compute(input_text: str, upstream: dict, llm, timeout: float = 10.0) -> dict:
    """LLM 生成代码 → 沙箱执行；失败回喂 ≤1 次。返回 {ok, code, result_text, error, attempts}。"""
    desc = _data_desc(upstream)
    code, err = "", ""
    for attempt in (1, 2):
        prompt = render(load_prompt("compute"), input=input_text, data_desc=desc)
        if err:
            prompt += f"\n\n[上次代码执行失败，请修正后重新输出完整代码：{err}]"
        raw = _chat(llm, SYSTEM, prompt, max_tokens=2048)
        code = _extract_code(raw)
        out = sandbox.run_python_calc(code, upstream, timeout=timeout)
        if out["ok"]:
            return {
                "ok": True,
                "code": code,
                "result_text": str(out["result"])[:500],
                "error": "",
                "attempts": attempt,
            }
        err = out["error"]
    return {"ok": False, "code": code, "result_text": "", "error": err[:300], "attempts": attempt}


# ================================================================ search_doc
def search_doc(query: str, top_k: int = 3) -> list[dict]:
    """口径文档检索：返回 [{title, source, score, text}]。"""
    st = load_settings()
    ecfg = st.get("models", {}).get("embed", {})
    backend = ecfg.get("backend", "mock")
    idx = VectorIndex.load(INDEX_DIR, "doc_index")
    if idx is None:
        raise RuntimeError(
            "doc_index 未构建：先运行 scripts/build_docs.py 与 scripts/build_doc_index.py"
        )
    qv = get_embedder(backend, ecfg.get("model"), ecfg.get("base_url")).embed([query])[0]
    if qv.shape[0] != idx.dim:
        raise RuntimeError(f"doc_index 维度（{idx.dim}）与当前嵌入后端不一致，请重建索引")
    hits = idx.search(qv, k=top_k)
    return [
        {
            "title": h.get("title", ""),
            "source": h.get("source", ""),
            "score": round(h["score"], 4),
            "text": h.get("text", ""),
        }
        for h in hits
    ]
