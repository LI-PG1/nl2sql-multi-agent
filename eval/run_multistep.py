"""多步任务评测（Phase C 完整判分）：完成率 / 步骤序列 / 最终答案 / 编排增益对照。

判定口径：
- 完成：final.path ∈ {normal, degrade} 且答案非空 且 计划步骤全部执行；
- 步骤序列：期望关键步骤按序覆盖于实际序列（允许合理的额外步骤，如前置口径检索）；
- 最终答案：gold 数值全部出现（1% 容差 + 数量级缩放匹配）；
- 无编造数字：综合引用数字全部来自观察（作为过程指标单列，不单独否决答案）；
- 编排增益：同任务集上 编排链路 vs 无规划基线（单发直答）的 gold 匹配率对照。

用法：
    python eval/run_multistep.py --mock          # 离线干跑（harness 验证，数字无意义）
    python eval/run_multistep.py                 # 真实 LLM 全量 + 基线对照
    python eval/run_multistep.py --limit 5 --no-naive
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

from dotenv import load_dotenv

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.graph import run as run_nl2sql  # noqa: E402
from src.llm import LLM  # noqa: E402
from src.orchestrator import MockOrchLLM, extract_nums, num_match, run_task  # noqa: E402
from src.pipeline import DB_PATH, run_sql  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
TASKS = ROOT / "eval" / "multistep_tasks.jsonl"
GOLD_CACHE = ROOT / "eval" / "answers" / "multistep_gold.jsonl"


def load_tasks() -> list[dict]:
    return [
        json.loads(line) for line in TASKS.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def load_gold() -> dict[str, list[float]]:
    if GOLD_CACHE.exists():
        rows = [
            json.loads(line)
            for line in GOLD_CACHE.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return {x["id"]: x["values"] for x in rows}
    gold: dict[str, list[float]] = {}
    for t in load_tasks():  # 无缓存 → 现场执行 gold_sql
        vals: list[float] = []
        for q in t.get("gold_sql", []):
            _, rows = run_sql(q, DB_PATH)
            vals.extend(float(c) for r in rows for c in r if isinstance(c, (int, float)))
        gold[t["id"]] = vals
    return gold


def gold_check(answer: str, gold: list[float]) -> tuple[bool, list[float]]:
    nums = extract_nums(answer)
    missing = [g for g in gold if not any(num_match(n, g) for n in nums)]
    return (len(gold) > 0 and not missing), missing


def is_subsequence(expected: list, actual: list) -> bool:
    """期望关键步骤按序覆盖于实际序列（允许合理附加步骤，如前置口径检索）。"""
    i = 0
    for x in actual:
        if i < len(expected) and x == expected[i]:
            i += 1
    return i == len(expected)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true", help="离线干跑（harness 验证）")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-naive", action="store_true", help="跳过无规划基线对照")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    cases = load_tasks()
    if args.limit:
        cases = cases[: args.limit]
    gold = load_gold()
    llm = MockOrchLLM() if args.mock else LLM()
    out_path = (
        ROOT
        / "eval"
        / (args.out or ("report_multistep_mock.md" if args.mock else "report_multistep.md"))
    )
    print(
        f"[run_multistep] 用例 {len(cases)} 条｜模式={'Mock' if args.mock else '真实 LLM'}"
        + ("" if args.mock or args.no_naive else "｜含无规划基线对照")
    )

    results = []
    t_all0 = time.time()
    for i, c in enumerate(cases, 1):
        t0 = time.perf_counter()
        try:
            r = run_task(c["task"], llm=llm, save_sample=c["id"])
        except Exception as e:  # noqa: BLE001  单题异常不中断整轮
            r = {
                "trace": {
                    "plan": [],
                    "steps": [],
                    "layers": [],
                    "synthesize": {},
                    "final": {"path": f"error:{e}"[:40], "answer": ""},
                },
                "final": {"path": f"error:{e}"[:40], "answer": ""},
                "trace_file": "-",
            }
        ms = (time.perf_counter() - t0) * 1000
        tr, fin = r["trace"], r["final"]
        planned = len(tr.get("plan", []))
        executed = len(tr.get("steps", []))
        skills = [s.get("skill") for s in tr.get("steps", [])]
        completed = (
            fin.get("path") in ("normal", "degrade")
            and bool(fin.get("answer"))
            and planned >= 1
            and executed == planned
        )
        seq_ok = is_subsequence(c["expected_skills"], skills)
        answer = fin.get("answer") or ""
        gold_ok, missing = gold_check(answer, gold.get(c["id"], []))
        unverified = (tr.get("synthesize") or {}).get("unverified_numbers", [])
        answer_ok = gold_ok  # 判定对齐设计 §5.1（数值容差比对）；无编造作为过程指标单列
        all_ok = completed and answer_ok
        rec = {
            "id": c["id"],
            "type": c["type"],
            "task": c["task"],
            "completed": completed,
            "seq_ok": seq_ok,
            "gold_ok": gold_ok,
            "answer_ok": answer_ok,
            "all_ok": all_ok,
            "path": fin.get("path"),
            "planned": planned,
            "executed": executed,
            "skills": skills,
            "missing": missing[:6],
            "unverified": unverified,
            "failed_steps": [s.get("step_id") for s in tr.get("steps", []) if not s.get("ok")],
            "layers": tr.get("layers", []),
            "ms": round(ms, 1),
            "answer_head": answer[:150],
            "trace_file": r["trace_file"],
        }
        results.append(rec)
        flag = "√" if all_ok else "×"
        miss_txt = ", ".join(f"{x:g}" for x in missing[:3]) if missing else "—"
        print(
            f"[{i:02d}/{len(cases)}] {c['id']} {flag} 完成={'Y' if completed else 'N'}"
            f" 序列={'Y' if seq_ok else 'N'} 答案={'Y' if gold_ok else 'N'}（缺 {miss_txt}）"
            f" {ms:6.0f}ms｜{c['task'][:34]}"
        )

    naive: list = []
    if not args.mock and not args.no_naive:
        print("\n[run_multistep] 无规划基线（单发直答）对照...")
        for i, c in enumerate(cases, 1):
            t0 = time.perf_counter()
            try:
                rr = run_nl2sql(c["task"], mode="eval", llm=llm)
                ans = (rr["final"].get("answer") or "").strip()
                path = rr["final"].get("path")
            except Exception as e:  # noqa: BLE001
                ans, path = "", f"err:{e}"[:60]
            ms = (time.perf_counter() - t0) * 1000
            gold_ok_n, missing_n = gold_check(ans, gold.get(c["id"], []))
            naive.append(
                {
                    "id": c["id"],
                    "gold_ok": gold_ok_n,
                    "path": path,
                    "missing": missing_n[:4],
                    "ms": round(ms, 1),
                    "answer_head": ans[:120],
                }
            )
            print(
                f"[naive {i:02d}/{len(cases)}] {c['id']} {'√' if gold_ok_n else '×'}"
                f" {ms:6.0f}ms｜{c['task'][:36]}"
            )

    elapsed = time.time() - t_all0
    _write_report(args, results, naive, elapsed, out_path)

    det = (
        ROOT / "eval" / ("multistep_details_mock.jsonl" if args.mock else "multistep_details.jsonl")
    )
    with open(det, "w", encoding="utf-8") as f:
        for rec in results:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        for rec in naive:
            f.write(json.dumps({"naive": True, **rec}, ensure_ascii=False) + "\n")

    n_all = sum(r["all_ok"] for r in results)
    print(
        f"\n[run_multistep] 全通过 {n_all}/{len(results)}"
        f"｜完成 {sum(r['completed'] for r in results)}｜序列 {sum(r['seq_ok'] for r in results)}"
        f"｜答案 {sum(r['answer_ok'] for r in results)}"
        + (f"｜基线 gold 匹配 {sum(n['gold_ok'] for n in naive)}/{len(naive)}" if naive else "")
        + f"｜耗时 {elapsed / 60:.1f} min"
    )


def _write_report(args, results: list, naive: list, elapsed: float, out_path: pathlib.Path) -> None:
    n = len(results)

    def cnt(key: str) -> int:
        return sum(r[key] for r in results)

    lines = [
        f"# 多步任务评测报告（{'Mock 干跑（数字无意义）' if args.mock else '真实 LLM'} · 完整判分）",
        "",
        f"- 日期：{time.strftime('%Y-%m-%d %H:%M')}｜用例：{n} 条｜总耗时：{elapsed / 60:.1f} min",
        "- 判定：完成＝路径正常且步骤全执行；序列＝期望关键步骤按序覆盖（允许合理附加）；"
        "答案＝gold 数值全出现（1% 容差）；全通过＝完成 ∧ 答案；无编造数字另列过程指标",
        "",
        "## 总指标",
        "",
        "| 指标 | 数值 |",
        "|---|---|",
        f"| 任务完成率 | {cnt('completed')}/{n} = {cnt('completed') / max(1, n):.1%} |",
        f"| 步骤序列覆盖率 | {cnt('seq_ok')}/{n} = {cnt('seq_ok') / max(1, n):.1%} |",
        f"| 最终答案正确率 | {cnt('answer_ok')}/{n} = {cnt('answer_ok') / max(1, n):.1%} |",
        f"| **全通过率（完成 ∧ 答案）** | **{cnt('all_ok')}/{n} = {cnt('all_ok') / max(1, n):.1%}** |",
        f"| 无编造数字（过程指标） | {sum(1 for r in results if not r['unverified'])}/{n} |",
    ]
    if naive:
        ng = sum(x["gold_ok"] for x in naive)
        lines += [
            "",
            "## 编排增益对照（gold 匹配率）",
            "",
            "| 口径 | 编排链路 | 无规划基线（单发直答） |",
            "|---|---|---|",
            f"| gold 匹配 | {cnt('gold_ok')}/{n}（{cnt('gold_ok') / max(1, n):.1%}） | "
            f"{ng}/{n}（{ng / max(1, n):.1%}） |",
            f"| 失败率 | {(n - cnt('gold_ok')) / n:.1%} | {(n - ng) / n:.1%} |",
        ]
    types: dict[str, list] = {}
    for r in results:
        types.setdefault(r["type"], []).append(r)
    lines += ["", "## 分层（按类型）", "", "| 类型 | 条数 | 全通过 | 通过率 |", "|---|---|---|---|"]
    for tp, rs in types.items():
        p = sum(r["all_ok"] for r in rs)
        lines.append(f"| {tp} | {len(rs)} | {p} | {p / len(rs):.1%} |")

    fails = [r for r in results if not r["all_ok"]]
    lines += ["", f"## 失败清单（{len(fails)} 条）", ""]
    if not fails:
        lines.append("（无失败）")
    else:
        lines += ["| id | 类型 | 失败点 | 详情 |", "|---|---|---|---|"]
        for r in fails:
            pts = []
            if not r["completed"]:
                pts.append("完成")
            if not r["answer_ok"]:
                pts.append("答案")
            if not r["seq_ok"]:
                pts.append("序列")
            detail = ""
            if r["missing"]:
                detail += "缺: " + ", ".join(f"{x:g}" for x in r["missing"][:3])
            if r["unverified"]:
                detail += (
                    ("；" if detail else "")
                    + "编造: "
                    + ", ".join(str(x) for x in r["unverified"][:3])
                )
            if r["failed_steps"]:
                detail += ("；" if detail else "") + "挂步: " + ",".join(r["failed_steps"])
            lines.append(f"| {r['id']} | {r['type']} | {'+'.join(pts)} | {detail[:70]} |")

    lines += ["", "## 回答摘录", ""]
    for r in results:
        mark = "√" if r["all_ok"] else "×"
        lines.append(
            f"- {mark} **{r['id']}**（{r['type']}）：{r['answer_head'].replace(chr(10), ' ')[:110]}…"
        )
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[run_multistep] 报告 → {out_path}")


if __name__ == "__main__":
    main()
