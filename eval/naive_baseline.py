"""朴素基线（对照实验用）：单次 LLM 直出 SQL——无 Schema Linking / 无 few-shot / 无重试 / 无澄清。

用法：
    python eval/naive_baseline.py --mock     # 干跑（harness 验证）
    python eval/naive_baseline.py            # 真实 LLM（需 .env）

对照：与 eval/run_eval.py（完整编排）在同一评测集上的执行准确率比较
     → 量化"编排（链接 + few-shot + 重试 + 澄清）带来的增益"。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from run_eval import execute, norm_rows, rows_eq  # noqa: E402  （复用判分口径）

from src.guard import validate_sql  # noqa: E402
from src.llm import LLM, MockLLM  # noqa: E402
from src.pipeline import build_schema_text, extract_sql  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "eval" / "golden_set.jsonl"
CACHE = ROOT / "eval" / "answers" / "golden_results.jsonl"
NAIVE_TYPES = ("basic", "join", "filter")

SYS = (
    "你是数据分析助手。根据给定的数据库表结构，把用户问题翻译为一条 SQLite 只读查询。"
    "只输出 SQL，不要任何解释。"
)


def build_prompt(question: str, schema: str) -> str:
    return f"[数据库表结构]\n{schema}\n\n[问题]\n{question}\n\n[SQL]"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true")
    args = ap.parse_args()

    cases = [
        json.loads(line)
        for line in GOLDEN.read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line)["type"] in NAIVE_TYPES
    ]
    cache: dict[str, list] = {}
    if CACHE.exists():
        for line in CACHE.read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                cache[d["id"]] = [tuple(r) for r in d["rows"]]

    llm = MockLLM() if args.mock else LLM()
    schema = build_schema_text()
    print(
        f"[naive] 用例 {len(cases)} 条｜模式={'Mock' if args.mock else '真实 LLM'}｜单次直出（无编排）"
    )

    results, ms_list = [], []
    for i, c in enumerate(cases, 1):
        t0 = time.perf_counter()
        ok, why = False, ""
        try:
            raw = llm.chat(SYS, build_prompt(c["question"], schema))
            sql = extract_sql(raw)
            good, cleaned_or_err = validate_sql(sql)
            if not good:
                why = f"守卫拦截:{cleaned_or_err[:40]}"
            else:
                g_rows = norm_rows(cache.get(c["id"]) or execute(c["sql"]))
                a_rows = norm_rows(execute(cleaned_or_err))
                ok = rows_eq(g_rows, a_rows) and len(g_rows) > 0
                why = "" if ok else "结果集不一致"
        except Exception as e:  # noqa: BLE001
            why = f"错误:{str(e)[:50]}"
        ms = (time.perf_counter() - t0) * 1000
        ms_list.append(ms)
        results.append({"id": c["id"], "ok": ok, "why": why, "ms": ms})
        print(
            f"[{i:02d}/{len(cases)}] {'PASS' if ok else 'FAIL'} {ms:7.0f}ms  {c['question'][:34]}"
            + ("" if ok else f"  ← {why}")
        )

    n_pass = sum(r["ok"] for r in results)
    ms_sorted = sorted(ms_list) or [0]

    def pctl(p: float) -> float:
        return ms_sorted[min(len(ms_sorted) - 1, int(len(ms_sorted) * p))]

    out = ROOT / "eval" / ("report_naive_dryrun.md" if args.mock else "report_naive.md")
    lines = [
        "# 朴素基线报告（单次直出，无编排）",
        "",
        f"- 日期：{time.strftime('%Y-%m-%d %H:%M')}｜用例：{len(cases)} 条（basic/join/filter）",
        f"- 模式：{'dry-run（Mock）' if args.mock else '真实 LLM'}",
        f"- 执行准确率：{n_pass}/{len(cases)} = {n_pass / max(1, len(cases)):.1%}",
        f"- 时延：P50 = {pctl(0.5):.0f} ms｜P95 = {pctl(0.95):.0f} ms",
        "",
        "> 与 `report_v1.md`（完整编排）对比即得编排增益（同评测集）。",
    ]
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[naive] 执行准确率 {n_pass}/{len(cases)}｜报告 → {out}")


if __name__ == "__main__":
    main()
