"""LangGraph 全链路（M2 骨架）：起点 → 展开 → 收束。

- 起点：单一入口（question + mode），状态初始化；
- 展开：主干单向推进（理解 → 链接 → 生成 → 执行 → 解读）；
        两条回路就近回退——澄清（歧义 → 追问 → 消解后回理解）、重试（失败 → 错误分类 → 回生成）；
- 收束：唯一出口 respond（正常 / 降级 / 拒答三路汇入），运行记录（trace）落 runs/。

运行（离线干跑，三条路径演示：澄清 / 重试 / 拒答）：
    python -m src.graph
"""

from __future__ import annotations

import json
import pathlib
import time
import uuid
from typing import TypedDict

from langgraph.graph import END, StateGraph

from . import fewshots, linking, metrics
from .config import load_settings
from .guard import validate_sql
from .llm import MockLLM
from .pipeline import DB_PATH, extract_sql, run_sql
from .prompts import SYSTEM, load_prompt, render

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNS_DIR = ROOT / "runs"


class State(TypedDict, total=False):
    # 输入
    question: str
    mode: str  # interactive | eval
    # 理解层
    metric_hits: dict
    ambiguity: str | None  # 歧义组 id
    clarified: str
    clarify_count: int
    clarify_info: dict
    refuse_hit: str  # 变更类意图命中词（拒答路径）
    # 检索层
    candidate_schema: dict
    few_shots: list
    # 生成与执行
    sql: str
    error: str
    error_type: str
    retries: int
    retry_history: list
    exec_ok: bool
    columns: list
    rows: list
    # 输出与观测
    answer: str
    path: str  # normal | degrade | refuse
    node_log: list
    timings: dict
    done: bool


def _classify_guard(msg: str) -> str:
    if "禁止关键字" in msg:
        return "guard:forbidden"
    if "多语句" in msg or "单条" in msg:
        return "guard:multi_statement"
    if "SELECT" in msg:
        return "guard:not_select"
    return "guard:parse"


def _classify_exec(msg: str) -> str:
    low = msg.lower()
    if "no such table" in low:
        return "exec:no_such_table"
    if "no such column" in low:
        return "exec:no_such_column"
    if "syntax error" in low:
        return "exec:syntax"
    return "exec:other"


def _summarize(name: str, out: dict) -> dict:
    if name == "understand":
        return {
            "refuse_hit": out.get("refuse_hit"),
            "metric_hits": out.get("metric_hits"),
            "ambiguity": out.get("ambiguity"),
        }
    if name == "clarify":
        return {
            "group": (out.get("clarify_info") or {}).get("group"),
            "answer": out.get("clarified"),
        }
    if name == "link_schema":
        cs = out.get("candidate_schema") or {}
        return {
            "tables": cs.get("tables"),
            "fields_n": len(cs.get("fields", [])),
            "source": cs.get("source"),
        }
    if name == "gen_sql":
        return {"sql": out.get("sql")}
    if name == "exec_sql":
        return {
            "ok": out.get("exec_ok", False),
            "error_type": out.get("error_type"),
            "rows_n": len(out.get("rows", []) or []),
        }
    if name == "interpret":
        return {"answer_head": (out.get("answer") or "")[:60]}
    if name == "respond":
        return {"path": out.get("path")}
    return {}


def build_graph(llm, db_path=DB_PATH, ask=None):
    st = load_settings()
    max_retries = st.get("retry", {}).get("max_retries", 2)
    max_clarify = st.get("clarify", {}).get("max_rounds", 1)

    def timed(name, fn):
        def wrapped(state: State) -> dict:
            t0 = time.perf_counter()
            out = fn(state) or {}
            ms = (time.perf_counter() - t0) * 1000
            out["node_log"] = list(state.get("node_log", [])) + [
                {"name": name, "ms": round(ms, 1), "out": _summarize(name, out)}
            ]
            timings = dict(state.get("timings", {}))
            timings[name] = timings.get(name, 0) + ms
            out["timings"] = timings
            return out

        return wrapped

    def understand(state: State) -> dict:
        q = state["question"]
        hit = metrics.detect_mutation(q)
        if hit:
            return {"refuse_hit": hit}
        mh = metrics.detect_metrics(q)
        amb = metrics.detect_ambiguity(q, clarified=state.get("clarified"))
        if amb and state.get("clarify_count", 0) >= max_clarify:
            amb = None  # 澄清超限：按默认口径继续主干
        return {"metric_hits": mh, "ambiguity": amb["id"] if amb else None}

    def clarify(state: State) -> dict:
        group = metrics.get_group(state["ambiguity"])
        question_text = metrics.clarify_question(group)
        if state.get("mode") == "interactive" and ask is not None:
            answer = ask(question_text)
        else:
            answer = "默认口径"
        info = {"fired": True, "group": group["id"], "question": question_text, "answer": answer}
        return {
            "clarified": answer,
            "clarify_count": state.get("clarify_count", 0) + 1,
            "clarify_info": info,
        }

    def link_schema(state: State) -> dict:
        q = state["question"]
        if state.get("clarified"):
            q += " " + state["clarified"]
        cs = linking.select_candidate_schema(q)
        fs = fewshots.select_fewshots(q, cs.get("tables"))
        return {"candidate_schema": cs, "few_shots": fs}

    def gen_sql(state: State) -> dict:
        retry = bool(state.get("error"))
        tpl = load_prompt("retry_sql" if retry else "gen_sql")
        cs = state.get("candidate_schema") or {}
        context = metrics.build_context(state["question"], state.get("clarified"))
        q_text = state["question"]
        if state.get("clarified"):
            q_text += f"（澄清补充：{state['clarified']}）"
        prompt = render(
            tpl,
            schema_text=cs.get("schema_text", ""),
            metric_context=context,
            few_shots=fewshots.format_examples(state.get("few_shots", [])) or "（无）",
            question=q_text,
            error=state.get("error", ""),
            error_type=state.get("error_type", ""),
            last_sql=state.get("sql", ""),
        )
        raw = llm.chat(SYSTEM, prompt)
        return {"sql": extract_sql(raw)}

    def exec_sql(state: State) -> dict:
        candidate = state.get("sql", "")
        ok, cleaned_or_err = validate_sql(candidate)
        if not ok:
            et = _classify_guard(cleaned_or_err)
            n = state.get("retries", 0) + 1
            return {
                "error": cleaned_or_err,
                "error_type": et,
                "exec_ok": False,
                "retries": n,
                "retry_history": state.get("retry_history", [])
                + [{"n": n, "error_type": et, "sql": candidate}],
            }
        try:
            cols, rows = run_sql(cleaned_or_err, db_path)
            return {
                "sql": cleaned_or_err,
                "columns": cols,
                "rows": rows,
                "error": "",
                "error_type": "",
                "exec_ok": True,
            }
        except Exception as e:  # noqa: BLE001
            et = _classify_exec(str(e))
            n = state.get("retries", 0) + 1
            return {
                "error": str(e),
                "error_type": et,
                "exec_ok": False,
                "retries": n,
                "retry_history": state.get("retry_history", [])
                + [{"n": n, "error_type": et, "sql": candidate}],
            }

    def interpret(state: State) -> dict:
        rows = state.get("rows") or []
        if not rows:
            return {"answer": "查询未返回数据（结果集为空）。"}
        context = metrics.build_context(state["question"], state.get("clarified"))
        preview = "\n".join(str(r) for r in rows[:10])
        prompt = render(
            load_prompt("interpret"),
            question=state["question"],
            metric_context=context,
            sql=state.get("sql", ""),
            rows_preview=preview,
        )
        ans = (llm.chat(SYSTEM, prompt) or "").strip()
        if not ans:
            ans = f"查询完成，返回 {len(rows)} 行；示例：{rows[0]}"
        return {"answer": ans}

    def respond(state: State) -> dict:
        if state.get("refuse_hit"):
            answer = (
                f"该请求涉及数据变更（命中「{state['refuse_hit']}」），"
                "本助手仅支持只读查询，未执行任何操作。"
            )
            path = "refuse"
        elif state.get("exec_ok"):
            answer = state.get("answer") or ""
            path = "normal"
        else:
            answer = (
                "未能在限定次数内完成查询，已降级输出。\n"
                f"最后一次错误：{state.get('error_type') or '执行失败'}｜{state.get('error') or ''}\n"
                "已尝试的 SQL 见运行记录。"
            )
            path = "degrade"
        return {"answer": answer, "path": path, "done": True}

    def route_understand(state: State) -> str:
        if state.get("refuse_hit"):
            return "refuse"
        if state.get("ambiguity"):
            return "clarify"
        return "proceed"

    def route_exec(state: State) -> str:
        if state.get("exec_ok"):
            return "done"
        if state.get("retries", 0) < max_retries:
            return "retry"
        return "giveup"

    g = StateGraph(State)
    g.add_node("understand", timed("understand", understand))
    g.add_node("clarify", timed("clarify", clarify))
    g.add_node("link_schema", timed("link_schema", link_schema))
    g.add_node("gen_sql", timed("gen_sql", gen_sql))
    g.add_node("exec_sql", timed("exec_sql", exec_sql))
    g.add_node("interpret", timed("interpret", interpret))
    g.add_node("respond", timed("respond", respond))
    g.set_entry_point("understand")
    g.add_conditional_edges(
        "understand",
        route_understand,
        {"refuse": "respond", "clarify": "clarify", "proceed": "link_schema"},
    )
    g.add_edge("clarify", "understand")
    g.add_edge("link_schema", "gen_sql")
    g.add_edge("gen_sql", "exec_sql")
    g.add_conditional_edges(
        "exec_sql", route_exec, {"done": "interpret", "retry": "gen_sql", "giveup": "respond"}
    )
    g.add_edge("interpret", "respond")
    g.add_edge("respond", END)
    return g.compile()


def save_trace(trace: dict, sample_name: str | None = None) -> pathlib.Path:
    if sample_name:
        out_dir = RUNS_DIR / "samples"
        fp = out_dir / f"{sample_name}.json"
    else:
        out_dir = RUNS_DIR
        # 唯一后缀：并行步骤同一秒写 trace 时避免文件名冲突（Windows 文件锁会报 PermissionError）
        fp = out_dir / f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.json"
    out_dir.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
    return fp


def run(
    question: str,
    mode: str = "interactive",
    llm=None,
    ask=None,
    db_path=DB_PATH,
    save_sample: str | None = None,
) -> dict:
    """跑一次完整 Pipeline；返回 {trace, final, trace_file}。"""
    llm = llm or MockLLM()
    graph = build_graph(llm, db_path=db_path, ask=ask)
    state = graph.invoke(
        {
            "question": question,
            "mode": mode,
            "retries": 0,
            "clarify_count": 0,
            "node_log": [],
            "timings": {},
        }
    )
    trace = {
        "question": question,
        "mode": mode,
        "nodes": state.get("node_log", []),
        "clarify": state.get("clarify_info", {"fired": False}),
        "retries": state.get("retry_history", []),
        "final": {
            "path": state.get("path"),
            "sql": state.get("sql", ""),
            "rows_n": len(state.get("rows") or []),
            "answer": state.get("answer", ""),
        },
        "timings": {k: round(v, 1) for k, v in state.get("timings", {}).items()},
    }
    trace_file = save_trace(trace, sample_name=save_sample)
    return {"trace": trace, "final": state, "trace_file": str(trace_file)}


def _print_result(name: str, res: dict) -> None:
    t, f = res["trace"], res["final"]
    total_ms = sum(t["timings"].values())
    print(f"\n[{name}] 路径={f.get('path')}｜总耗时≈{total_ms:.0f}ms")
    if f.get("sql"):
        print(f"  SQL: {f['sql']}")
    print(f"  行数: {len(f.get('rows') or [])}｜回答: {(f.get('answer') or '')[:72]}")
    print(f"  运行记录: {res['trace_file']}")


if __name__ == "__main__":
    # 干跑模式：检索后端一并切到本地 mock，避免依赖未启动的远程嵌入服务
    from .config import load_settings

    load_settings()["models"]["embed"]["backend"] = "mock"

    print("=== 干跑①：澄清路径（金额歧义 → 追问 → 消解后生成）===")

    def ask(q: str) -> str:
        print("  【澄清追问】" + q)
        return "支付总额"

    r1 = run(
        "平台的销售额是多少？",
        mode="interactive",
        llm=MockLLM(),
        ask=ask,
        save_sample="clarify_path",
    )

    print("=== 干跑②：重试路径（首轮错表名 → 错误反馈 → 修正成功）===")
    r2 = run(
        "2018 年各州订单量排名？", llm=MockLLM(simulate_first_error=True), save_sample="retry_path"
    )

    print("=== 干跑③：拒答路径（变更类请求）===")
    r3 = run("把取消的订单从库里删掉", llm=MockLLM(), save_sample="refuse_path")

    _print_result("澄清路径", r1)
    _print_result("重试路径", r2)
    _print_result("拒答路径", r3)

    ok = all(
        [
            r1["trace"]["clarify"]["fired"] and r1["final"]["path"] == "normal",
            len(r2["trace"]["retries"]) == 1 and r2["final"]["path"] == "normal",
            r3["final"]["path"] == "refuse",
        ]
    )
    print("\n全链干跑：" + ("PASS（澄清 / 重试 / 拒答 三路径均通）" if ok else "FAIL"))
