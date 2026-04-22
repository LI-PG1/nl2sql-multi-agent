"""外层任务编排（升维 §3）：plan → execute_step → synthesize → respond。

- 技能层（src/skills）：query_data（NL2SQL 链路整体包装）/ compute（Python 沙箱）/ search_doc（口径检索）；
- 执行：按依赖分批（同批并行，Semaphore 控并发）；上游结果自动注入下游（文本参考 / compute 的 data 变量）；
  单步失败重试 ≤1；仍败则标记降级继续（不中断全任务）；拒绝变更类请求；
- 纪律：max_steps=8；计划 2–6 步，解析失败带错重试 1 次 → 单步兜底计划；
- 可观测：DAG 级 trace（计划 / 分批并行关系 / 每步观察与告警 / 综合与数字校验）落 runs/。

运行：
    python -m src.orchestrator                 # 离线干跑（Mock）
    python -m src.orchestrator --real          # 真实 LLM（读取 .env 端点）
    python -m src.orchestrator --task "问题..."  # 单任务运行
"""

from __future__ import annotations

import json
import pathlib
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import TypedDict

from langgraph.graph import END, StateGraph

from . import metrics
from .graph import save_trace
from .llm import LLM, MockLLM
from .pipeline import DB_PATH
from .prompts import SYSTEM, load_prompt, render
from .skills import compute as compute_skill
from .skills import query_data, search_doc

ROOT = pathlib.Path(__file__).resolve().parents[1]
TASKS_PATH = ROOT / "eval" / "multistep_tasks.jsonl"

MAX_STEPS = 8  # 执行步数硬上限（设计 §3.2）
MAX_PLAN_STEPS = 6  # Planner 输出上限
MAX_PARALLEL = 3  # 同批并行上限（Semaphore 控并发）

# 技能注册表（plan.md 的 [可用技能] 由此渲染）
SKILLS = {
    "query_data": "自然语言取数：返回结果集 + 口径说明（含 Schema 链接 / SQL 生成 / 守卫 / 重试 / 解读）",
    "compute": "Python 计算：对上游步骤的数据做统计 / 运算（沙箱执行；上游数据以 data 变量注入）",
    "search_doc": "口径检索：检索口径手册与数据字典（指标定义、字段说明）",
}
ENV_NOTE = (
    "Olist 巴西电商库（orders / order_items / order_payments / customers / products 等 9 表）；"
    "金额默认口径＝order_payments.payment_value 合计；时间字段按下单时间；"
    "订单状态常用取值：delivered（已完成）；任务中的「已完成」＝order_status='delivered'；"
    "订单量＝COUNT(DISTINCT orders.order_id)；"
    "客户数＝COUNT(DISTINCT customers.customer_unique_id)（跨订单去重）；"
    "配送时长＝julianday(order_delivered_customer_date) − julianday(order_purchase_timestamp)（签收－下单）；"
    "品类翻译：category_translation（葡语 → 英语）。"
)

# 数字校验的容许刻度（覆盖 千 / 万 / 百万 / 亿 与 分数→百分比 等书写形式）
_SCALES = [1e-4, 1e-3, 1e-2, 1, 1e2, 1e3, 1e4, 1e6, 1e8]


def _chat(llm, system: str, user: str, max_tokens: int | None = None) -> str:
    """兼容 Mock 客户端（无 max_tokens 形参）与真实客户端（带生成上限纪律）。"""
    try:
        return (
            llm.chat(system, user, max_tokens=max_tokens) if max_tokens else llm.chat(system, user)
        )
    except TypeError:
        return llm.chat(system, user)


def extract_nums(text: str) -> list[float]:
    """提取数字（含千分位）为数值列表。"""
    out: list[float] = []
    for m in re.findall(r"\d[\d,]*(?:\.\d+)?", text or ""):
        try:
            out.append(float(m.replace(",", "")))
        except ValueError:
            pass
    return out


def num_match(n: float, o: float) -> bool:
    """数值匹配：允许 1% 相对容差 + 常见数量级刻度（万 / 百万 / 亿 / 百分比形式）。"""
    tol = max(0.05, abs(o) * 0.01)
    return any(abs(n * s - o) <= tol for s in _SCALES)


class TaskState(TypedDict, total=False):
    task: str
    mode: str
    refuse_hit: str
    plan: list
    plan_fallback: bool
    done_ids: list
    layers: list
    step_records: list
    synth: dict
    synth_failed: bool
    answer: str
    path: str
    done: bool


# ================================================================ 计划解析与校验
def _extract_json_array(raw: str):
    m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", raw, re.S | re.I)
    if m:
        raw = m.group(1)
    else:
        m = re.search(r"(\[.*\])", raw, re.S)
        if m:
            raw = m.group(1)
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else None
    except json.JSONDecodeError:
        return None


def validate_plan(data, skill_names) -> tuple[list | None, str]:
    """校验计划结构（步数 2–6，设计 §3.2）；依赖只允许指向之前步骤（保证 DAG 可执行）。"""
    if not isinstance(data, list) or not (2 <= len(data) <= MAX_PLAN_STEPS):
        n = len(data) if isinstance(data, list) else "非数组"
        return None, f"步数不合法（{n}，要求 2–{MAX_PLAN_STEPS}）"
    seen: set[str] = set()
    plan: list[dict] = []
    for i, s in enumerate(data):
        if not isinstance(s, dict):
            return None, f"第 {i + 1} 步不是对象"
        sid = str(s.get("id") or f"s{i + 1}")
        skill = s.get("skill")
        inp = str(s.get("input") or "").strip()
        dep = s.get("depends_on") or []
        if sid in seen:
            return None, f"id 重复：{sid}"
        if not isinstance(dep, list) or any(str(d) not in seen for d in dep):
            return None, f"{sid} 的 depends_on 非法（只允许依赖之前步骤）"
        if skill not in skill_names or not inp:
            return None, f"{sid} 的技能或 input 不合法"
        seen.add(sid)
        plan.append(
            {
                "id": sid,
                "skill": skill,
                "input": inp,
                "depends_on": [str(d) for d in dep],
                "expect": str(s.get("expect") or ""),
            }
        )
    return plan, ""


def _planner_prompt(task: str) -> str:
    skills_text = "\n".join(f"- {k}：{v}" for k, v in SKILLS.items())
    return render(load_prompt("plan"), skills=skills_text, env_note=ENV_NOTE, task=task)


def plan_task(task: str, llm) -> tuple[list, bool]:
    """返回 (plan, fallback)。解析失败 → 带错误重试 1 次 → 单步兜底计划。"""
    err = ""
    for _ in (1, 2):
        prompt = _planner_prompt(task)
        if err:
            prompt += f"\n\n[上次输出未通过校验：{err}。请只输出合法 JSON 数组。]"
        raw = _chat(llm, SYSTEM, prompt, max_tokens=1024)
        plan, err = validate_plan(_extract_json_array(raw), SKILLS)
        if plan:
            return plan, False
    return (
        [
            {
                "id": "s1",
                "skill": "query_data",
                "input": task,
                "depends_on": [],
                "expect": "尽力直接作答",
            }
        ],
        True,
    )


# ================================================================ 执行（依赖注入 + 并行分批）
def _text_ctx(deps: list) -> str:
    lines = []
    for d in deps:
        if d["skill"] == "query_data":
            brief = d.get("answer") or ""
            if d.get("rows_preview"):
                brief += f"；数据预览：{d['rows_preview'][:150]}"
            lines.append(f"- {d['step_id']}（取数）：{brief[:250]}")
        elif d["skill"] == "compute":
            lines.append(f"- {d['step_id']}（计算）：{d.get('result_text', '')[:200]}")
        elif d["skill"] == "search_doc":
            top = (d.get("hits") or [{}])[0]
            lines.append(f"- {d['step_id']}（口径检索）：{top.get('text', '')[:260]}")
    return "\n".join(lines)


def _doc_ctx(deps: list) -> str:
    parts = [
        d["hits"][0]["text"][:260] for d in deps if d["skill"] == "search_doc" and d.get("hits")
    ]
    return ("\n\n[口径检索参考]\n" + "\n".join(parts)) if parts else ""


def _verify(rec: dict) -> list[str]:
    """Verifier（规则校验）：空结果 / 截断 / NaN / 空计算 / 文档未命中。"""
    warns: list[str] = []
    if rec["skill"] == "query_data":
        if rec.get("rows_n") == 0:
            warns.append("空结果：建议下钻或确认口径")
        if rec.get("rows_n", 0) >= 200:
            warns.append("结果行数达显示上限（200），可能被截断")
        prev_low = (rec.get("rows_preview") or "").lower()
        if "nan" in prev_low:
            warns.append("结果含 NaN，请核对计算")
        if "none" in prev_low:
            warns.append("结果含 NULL，请核对筛选条件")
    elif rec["skill"] == "compute":
        rt = rec.get("result_text", "")
        if rt in ("None", ""):
            warns.append("计算结果为空（result 未赋值？）")
        if re.search(r"nan|inf", rt, re.I):
            warns.append("计算结果含 nan/inf")
    elif rec["skill"] == "search_doc" and rec.get("hits_n", 0) == 0:
        warns.append("文档未命中")
    return warns


def _rows_emptyish(rows) -> bool:
    """空结果判定：0 行，或所有行所有列均为 NULL（防"空聚合被静默当 0 继续合成"）。"""
    rows = rows or []
    if not rows:
        return True
    return all(all(c is None for c in r) for r in rows)


def _execute_one(step: dict, by_id: dict, llm, db_path) -> dict:
    """执行单步（含上游注入与步级重试）；任何异常不外抛（单步失败=降级记录）。"""
    t0 = time.perf_counter()
    rec = {
        "step_id": step["id"],
        "skill": step["skill"],
        "input": step["input"],
        "depends_on": list(step.get("depends_on", [])),
        "ok": False,
        "attempts": 0,
        "error": "",
        "warnings": [],
    }
    deps = [by_id[d] for d in step.get("depends_on", []) if d in by_id]
    try:
        if step["skill"] == "query_data":
            q = step["input"]
            ctx = _text_ctx(deps)
            if ctx:
                q = f"{q}\n\n[上游步骤参考结果]\n{ctx}"
            out = query_data(q, llm, db_path)
            rec["attempts"] = 1
            if out["path"] == "degrade":  # 步级重试 ≤1
                out = query_data(q, llm, db_path)
                rec["attempts"] = 2
            elif out["path"] == "normal" and _rows_emptyish(out.get("rows")):
                # 空结果视同软失败：带提示回喂重试一次
                q2 = q + (
                    "\n\n[提示] 上次查询结果为空（0 行或全 NULL）——请检查筛选条件"
                    "（时间范围 / 状态取值 / 字面量拼写）并按同一口径重新取数。"
                )
                out2 = query_data(q2, llm, db_path)
                rec["attempts"] = 2
                if out2.get("path") == "normal" and not _rows_emptyish(out2.get("rows")):
                    out = out2
            rec.update(
                {
                    "path": out["path"],
                    "sql": out["sql"],
                    "rows_n": out["rows_n"],
                    "rows_preview": out["rows_preview"],
                    "answer": out["answer"],
                    "rows": out["rows"],
                    "trace_file": out["trace_file"],
                }
            )
            if out["path"] == "normal":
                rec["ok"] = True
                if _rows_emptyish(out.get("rows")):
                    rec["warnings"].append(
                        "结果为空 / 全 NULL（重试后仍空——合成时按数据边界处理，勿臆造数值）"
                    )
            elif out["path"] == "refuse":
                rec["error"] = "步骤被判为变更类请求（拒答）"
            else:
                rec["error"] = "内部链路降级（执行未完成）"
        elif step["skill"] == "compute":
            data = {d["step_id"]: d.get("rows", []) for d in deps if "rows" in d}
            inp = step["input"] + _doc_ctx(deps)
            out = compute_skill(inp, data, llm)
            rec.update(
                {
                    "code": out["code"],
                    "result_text": out["result_text"],
                    "attempts": out["attempts"],
                }
            )
            rec["ok"] = out["ok"]
            rec["error"] = "" if out["ok"] else out["error"]
            if out["ok"]:
                trunc = [d["step_id"] for d in deps if d.get("rows_n", 0) >= 200]
                if trunc:
                    rec["warnings"].append(
                        f"上游 {'、'.join(trunc)} 结果可能被截断（200 行上限），计算结果或受影响"
                    )
        elif step["skill"] == "search_doc":
            hits = search_doc(step["input"])
            rec.update({"hits": hits, "hits_n": len(hits), "attempts": 1})
            rec["ok"] = len(hits) > 0
            if not rec["ok"]:
                rec["error"] = "文档未命中"
        else:
            rec["error"] = f"技能未注册：{step['skill']}"
    except Exception as e:  # noqa: BLE001
        rec["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    if rec["ok"]:
        rec["warnings"] = rec["warnings"] + _verify(rec)
    rec["ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return rec


def _obs_text(plan: list, rec_by_id: dict) -> str:
    lines = []
    for st in plan:
        rc = rec_by_id.get(st["id"])
        if rc is None:
            continue
        lines.append(f"- 步骤 {st['id']}（{st['skill']}）｜输入：{st['input']}")
        if not rc["ok"]:
            lines.append(f"  未完成：{rc['error']}")
        elif st["skill"] == "query_data":
            rows = rc.get("rows") or []
            prev = "; ".join(str(x) for x in rows[:20])[:1200] or "（空）"
            lines.append(f"  结果：{rc['rows_n']} 行；预览（前 {min(20, len(rows))} 行）：{prev}")
            lines.append(f"  解读：{rc['answer'][:200] or '（无）'}")
        elif st["skill"] == "compute":
            lines.append(f"  计算结果：{rc['result_text']}")
        elif st["skill"] == "search_doc":
            for h in (rc.get("hits") or [])[:2]:
                lines.append(
                    f"  口径（{h.get('source', '')}·{h.get('title', '')}）：{h.get('text', '')[:180]}"
                )
        if rc["warnings"]:
            lines.append("  告警：" + "；".join(rc["warnings"]))
    return "\n".join(lines)


def synthesize(task: str, plan: list, rec_by_id: dict, llm) -> dict:
    """Synthesizer：观察 → 结论；数字逐一校验（数量级容差匹配，未出现者入告警清单）。"""
    obs = _obs_text(plan, rec_by_id)
    prompt = render(
        load_prompt("synthesize"),
        task=task,
        plan_text=json.dumps(plan, ensure_ascii=False, indent=1),
        observations=obs,
    )
    answer = _chat(llm, SYSTEM, prompt, max_tokens=1024).strip()
    obs_nums = extract_nums(obs)
    unverified: list[float] = []
    for n in extract_nums(answer):
        if abs(n) < 1:
            continue
        if not any(num_match(n, o) for o in obs_nums):
            unverified.append(n)
    return {"answer": answer, "unverified_numbers": sorted(set(unverified))[:10], "failed": ""}


# ================================================================ 外层状态图
def build_task_graph(llm, db_path=DB_PATH):
    def plan_node(state: TaskState) -> dict:
        hit = metrics.detect_mutation(state["task"])
        if hit:
            return {"refuse_hit": hit}
        plan, fallback = plan_task(state["task"], llm)
        return {"plan": plan, "plan_fallback": fallback}

    def route_plan(state: TaskState) -> str:
        return "refuse" if state.get("refuse_hit") else "execute"

    def execute_layer(state: TaskState) -> dict:
        plan = state["plan"]
        done = list(state.get("done_ids", []))
        records = list(state.get("step_records", []))
        by_id = {r["step_id"]: r for r in records}
        ready = [s for s in plan if s["id"] not in done and all(d in done for d in s["depends_on"])]
        if not ready:  # 防御分支（计划校验已保证依赖有序，理论不可达）
            ready = [s for s in plan if s["id"] not in done][:1]
        results: dict = {}
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
            futs = {s["id"]: pool.submit(_execute_one, s, by_id, llm, db_path) for s in ready}
            for sid, fut in futs.items():
                try:
                    results[sid] = fut.result()
                except Exception as e:  # noqa: BLE001
                    results[sid] = {
                        "step_id": sid,
                        "skill": "?",
                        "input": "",
                        "ok": False,
                        "attempts": 0,
                        "error": f"executor:{e}"[:200],
                        "warnings": [],
                        "ms": 0.0,
                    }
        new_records = records + [results[s["id"]] for s in plan if s["id"] in results]
        done_new = done + [s["id"] for s in plan if s["id"] in results]
        layers = list(state.get("layers", [])) + [[s["id"] for s in plan if s["id"] in results]]
        return {"step_records": new_records, "done_ids": done_new, "layers": layers}

    def route_execute(state: TaskState) -> str:
        done = state.get("done_ids", [])
        more = any(s["id"] not in done for s in state.get("plan", [])) and len(done) < MAX_STEPS
        return "more" if more else "synth"

    def synthesize_node(state: TaskState) -> dict:
        by_id = {r["step_id"]: r for r in state.get("step_records", [])}
        try:
            s = synthesize(state["task"], state.get("plan", []), by_id, llm)
            return {"synth": s, "answer": s["answer"]}
        except Exception as e:  # noqa: BLE001  综合失败 → 降级为观察原文
            obs = _obs_text(state.get("plan", []), by_id)
            text = "（综合失败，以下为步骤观察原文）\n" + obs
            return {
                "synth": {
                    "answer": text,
                    "unverified_numbers": [],
                    "failed": f"{type(e).__name__}: {str(e)[:150]}",
                },
                "answer": text,
                "synth_failed": True,
            }

    def respond(state: TaskState) -> dict:
        if state.get("refuse_hit"):
            answer = (
                f"该请求涉及数据变更（命中「{state['refuse_hit']}」），"
                "编排层已拒答，未执行任何操作。"
            )
            return {"answer": answer, "path": "refuse", "done": True}
        failed = [r["step_id"] for r in state.get("step_records", []) if not r["ok"]]
        answer = state.get("answer") or ""
        if failed:
            answer += f"\n\n（注：步骤 {'、'.join(failed)} 未完成，结论可能不完整）"
            return {"answer": answer, "path": "degrade", "done": True}
        if state.get("synth_failed"):
            return {"answer": answer, "path": "degrade", "done": True}
        return {"answer": answer, "path": "normal", "done": True}

    g = StateGraph(TaskState)
    g.add_node("plan", plan_node)
    g.add_node("execute_step", execute_layer)
    g.add_node("synthesize", synthesize_node)
    g.add_node("respond", respond)
    g.set_entry_point("plan")
    g.add_conditional_edges("plan", route_plan, {"refuse": "respond", "execute": "execute_step"})
    g.add_conditional_edges(
        "execute_step", route_execute, {"more": "execute_step", "synth": "synthesize"}
    )
    g.add_edge("synthesize", "respond")
    g.add_edge("respond", END)
    return g.compile()


def run_task(
    task: str, mode: str = "eval", llm=None, db_path=DB_PATH, save_sample: str | None = None
) -> dict:
    """跑一次完整多步任务；返回 {trace, final, trace_file}（与单步 run 同构）。"""
    llm = llm or MockLLM()
    graph = build_task_graph(llm, db_path)
    t0 = time.perf_counter()
    state = graph.invoke(
        {"task": task, "mode": mode, "done_ids": [], "step_records": [], "layers": []}
    )
    total_ms = round((time.perf_counter() - t0) * 1000, 1)
    records = state.get("step_records", [])
    steps_trace = [{k: v for k, v in r.items() if k != "rows"} for r in records]
    trace = {
        "task": task,
        "mode": mode,
        "plan": state.get("plan", []),
        "plan_fallback": state.get("plan_fallback", False),
        "layers": state.get("layers", []),
        "steps": steps_trace,
        "synthesize": state.get("synth", {}),
        "final": {"path": state.get("path"), "answer": state.get("answer", "")},
        "timings": {"total_ms": total_ms},
    }
    trace_file = save_trace(trace, sample_name=save_sample)
    return {"trace": trace, "final": state, "trace_file": str(trace_file)}


# ================================================================ 干跑
class MockOrchLLM:
    """干跑：规划 / 综合 / 计算代码 返回演示输出；其余委托 MockLLM（内层 NL2SQL 链路）。"""

    def __init__(self):
        self.inner = MockLLM()

    def chat(self, system: str, user: str, temperature: float = 0.0) -> str:
        if "执行计划" in user and "可用技能" in user:
            mock_plan = [
                {
                    "id": "s1",
                    "skill": "query_data",
                    "input": "（干跑）第一步取数",
                    "depends_on": [],
                    "expect": "基础数据",
                },
                {
                    "id": "s2",
                    "skill": "compute",
                    "input": "（干跑）对上游数据求合计",
                    "depends_on": ["s1"],
                    "expect": "合计值",
                },
            ]
            return "```json\n" + json.dumps(mock_plan, ensure_ascii=False) + "\n```"
        if "观察记录" in user and "关键数字" in user:
            return "（干跑综合）已汇总各步骤观察，关键数字见结果预览；口径按默认约定。"
        if "生成一段 Python 计算代码" in user:
            return (
                "```python\n"
                "vals = [r[-1] if r else 0 for r in data.get('s1', [])]\n"
                "result = sum(vals)\n"
                "```"
            )
        return self.inner.chat(system, user)


if __name__ == "__main__":
    import sys

    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ImportError:
        pass

    real = "--real" in sys.argv
    llm = LLM() if real else MockOrchLLM()
    mode_tag = "真实 LLM" if real else "Mock"

    if "--task" in sys.argv:
        task_text = sys.argv[sys.argv.index("--task") + 1]
        tasks = [{"id": "adhoc", "task": task_text}]
    else:
        tasks = [
            json.loads(line)
            for line in TASKS_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ][:3]
    print(f"=== 多步编排干跑（{mode_tag}）：{len(tasks)} 条任务 ===")

    ok_all = True
    for t in tasks:
        r = run_task(t["task"], llm=llm, save_sample=t.get("id"))
        fin, tr = r["final"], r["trace"]
        print(f"\n[{t.get('id', '')}] {t['task']}")
        print(
            f"  计划：{len(tr['plan'])} 步（分批 {tr['layers']}）"
            + ("｜兜底计划" if tr["plan_fallback"] else "")
        )
        for s in tr["steps"]:
            mark = "OK  " if s["ok"] else "FAIL"
            detail = (
                s.get("sql") or s.get("result_text") or (s.get("hits") or [{}])[0].get("title", "")
            )
            print(
                f"    {mark} {s['step_id']} {s['skill']}: {s['input'][:38]}"
                f"（{str(detail)[:60]} / {s['ms']}ms）"
            )
            for w in s["warnings"]:
                print(f"         注意：{w}")
        print(f"  路径={fin.get('path')}｜回答：{(fin.get('answer') or '')[:100]}")
        if tr["synthesize"].get("unverified_numbers"):
            print(f"  注意：数字未在观察中出现：{tr['synthesize']['unverified_numbers']}")
        ok = fin.get("path") in ("normal", "degrade") and bool(fin.get("answer"))
        ok_all = ok_all and ok

    print("\n全链干跑：" + ("PASS（多步编排骨架运行正常）" if ok_all else "FAIL"))
