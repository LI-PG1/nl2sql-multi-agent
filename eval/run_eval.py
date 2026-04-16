"""一键评测：黄金集 → 报告（执行准确率 / 分层 / 过程指标）。

用法：
    python eval/run_eval.py             # 真实 LLM（需 .env 的 LLM_API_KEY）
    python eval/run_eval.py --mock      # 离线干跑（仅验证评测 harness，数字无意义）
    python eval/run_eval.py --limit 10  # 小样本先跑

判定口径（见 docs/详细设计.md §7.2）：
- refuse 类：路径 == refuse 即通过；
- ambiguous 类：澄清触发（clarify.fired）且正常收束即通过；
- 其余：生成 SQL 与参考 SQL 的结果集规范化比对（数值 round(2) / 文本去空白小写 / 行序无关；
  双方量级 ≥1 的数值容许 ±0.05 舍入容差，覆盖“一位小数”呈现口径）。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time

from dotenv import load_dotenv

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.graph import run  # noqa: E402
from src.llm import LLM, MockLLM  # noqa: E402
from src.pipeline import DB_PATH, run_sql  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")  # 读取 .env（JUDGE_* / LLM_*）
GOLDEN = ROOT / "eval" / "golden_set.jsonl"
CACHE = ROOT / "eval" / "answers" / "golden_results.jsonl"


def norm_cell(v):
    if v is None:
        return None
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return re.sub(r"\s+", "", str(v).strip().lower())


def norm_rows(rows) -> list:
    return sorted((tuple(norm_cell(c) for c in r) for r in rows), key=repr)


def cell_eq(a, b) -> bool:
    """数值近似比较：双方量级 ≥1 时容许 ±0.05（覆盖一位小数舍入口径）；其余严格相等。"""
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, float) and isinstance(b, float):
        if abs(a - b) <= 1e-9:
            return True
        return abs(a - b) <= 0.05 and min(abs(a), abs(b)) >= 1
    return a == b


def rows_eq(ga, aa) -> bool:
    """按 cell_eq 逐行逐列比较（行序无要求：两侧均已 norm_rows 排序）。"""
    if len(ga) != len(aa):
        return False
    for r1, r2 in zip(ga, aa):
        if len(r1) != len(r2) or not all(cell_eq(x, y) for x, y in zip(r1, r2)):
            return False
    return True


def execute(sql: str):
    _, rows = run_sql(sql, DB_PATH)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true", help="离线干跑（验证 harness）")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    cases = [
        json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    if args.limit:
        cases = cases[: args.limit]
    out_path = (
        pathlib.Path(args.out)
        if args.out
        else ROOT
        / "eval"
        / (
            "report_dryrun.md"
            if args.mock
            else ("report_v1_smoke.md" if args.limit else "report_v1.md")
        )
    )  # --limit 小回归不覆盖主报告

    cache: dict[str, list] = {}
    if CACHE.exists():
        for line in CACHE.read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                cache[d["id"]] = [tuple(r) for r in d["rows"]]

    llm = MockLLM() if args.mock else LLM()
    print(
        f"[run_eval] 用例 {len(cases)} 条｜模式={'Mock（harness 验证）' if args.mock else '真实 LLM'}"
    )

    results = []
    t_all0 = time.time()
    for i, c in enumerate(cases, 1):
        t0 = time.perf_counter()
        r = run(c["question"], mode="eval", llm=llm)
        ms = (time.perf_counter() - t0) * 1000
        tr, fin = r["trace"], r["final"]
        typ = c["type"]
        ok, why = False, ""
        if typ == "refuse":
            ok = fin.get("path") == "refuse"
            why = "" if ok else f"路径={fin.get('path')}"
        elif typ == "ambiguous":
            fired = bool(tr["clarify"].get("fired"))
            ok = fired and fin.get("path") in ("normal", "degrade")
            why = "" if ok else ("未触发澄清" if not fired else f"路径={fin.get('path')}")
        else:
            try:
                g_rows = norm_rows(cache.get(c["id"]) or execute(c["sql"]))
                a_rows = norm_rows(execute(fin.get("sql") or "SELECT 1 WHERE 0"))
                ok = rows_eq(g_rows, a_rows) and len(g_rows) > 0
                why = "" if ok else "结果集不一致"
            except Exception as e:  # noqa: BLE001
                ok, why = False, f"执行错误:{str(e)[:60]}"
        results.append(
            {
                "id": c["id"],
                "type": typ,
                "question": c["question"],
                "ok": ok,
                "ms": round(ms, 1),
                "why": why,
                "sql": fin.get("sql") or "",
                "path": fin.get("path"),
                "retries": len(tr.get("retries") or []),
                "clarify": bool(tr["clarify"].get("fired")),
            }
        )
        mark = "PASS" if ok else "FAIL"
        print(
            f"[{i:02d}/{len(cases)}] {typ:<9s} {mark} {ms:7.0f}ms  {c['question'][:34]}"
            + ("" if ok else f"  ← {why}")
        )

    # —— 汇总 ——
    per_type: dict[str, dict] = {}
    for r in results:
        d = per_type.setdefault(r["type"], {"n": 0, "pass": 0})
        d["n"] += 1
        d["pass"] += int(r["ok"])
    exec_types = ("basic", "join", "filter")
    exec_n = sum(per_type.get(t, {}).get("n", 0) for t in exec_types)
    exec_p = sum(per_type.get(t, {}).get("pass", 0) for t in exec_types)
    ms_list = sorted(r["ms"] for r in results) or [0]

    def pctl(p: float) -> float:
        return ms_list[min(len(ms_list) - 1, int(len(ms_list) * p))]

    retried = [r for r in results if r["retries"] > 0]
    retry_win = sum(1 for r in retried if r["ok"])
    elapsed = time.time() - t_all0

    lines = [
        f"# 评测报告（{'dry-run（Mock LLM，仅验证 harness）' if args.mock else '真实 LLM'}）",
        "",
        f"- 日期：{time.strftime('%Y-%m-%d %H:%M')}｜用例：{len(cases)} 条｜总耗时：{elapsed / 60:.1f} min",
        f"- Embedding：{_embed_backend()}｜检索 top-k：见 config/settings.yaml",
        "",
        "## 总指标",
        "",
        "| 指标 | 数值 |",
        "|---|---|",
        f"| 执行准确率（basic/join/filter，{exec_n} 条） | {exec_p}/{exec_n} = {exec_p / max(1, exec_n):.1%} |",
        f"| 澄清触发（ambiguous，{per_type.get('ambiguous', {}).get('n', 0)} 条） | {per_type.get('ambiguous', {}).get('pass', 0)}/{per_type.get('ambiguous', {}).get('n', 0)} |",
        f"| 拒答（refuse，{per_type.get('refuse', {}).get('n', 0)} 条） | {per_type.get('refuse', {}).get('pass', 0)}/{per_type.get('refuse', {}).get('n', 0)} |",
        f"| **总体通过** | **{sum(r['ok'] for r in results)}/{len(results)} = {sum(r['ok'] for r in results) / max(1, len(results)):.1%}** |",
        "",
        "## 分层结果",
        "",
        "| 类型 | 条数 | 通过 | 通过率 |",
        "|---|---|---|---|",
    ]
    for t, d in per_type.items():
        lines.append(f"| {t} | {d['n']} | {d['pass']} | {d['pass'] / max(1, d['n']):.1%} |")
    lines += [
        "",
        "## 过程指标",
        "",
        f"- 端到端时延：P50 = {pctl(0.5):.0f} ms｜P95 = {pctl(0.95):.0f} ms",
        f"- 重试增益：触发重试 {len(retried)} 条，其中重试后成功 {retry_win} 条",
        f"- 澄清记录：{sum(1 for r in results if r['clarify'])} 条触发澄清",
        "",
        "## 失败清单（前 20）",
        "",
    ]
    fails = [r for r in results if not r["ok"]]
    if not fails:
        lines.append("（无失败）")
    else:
        lines.append("| id | 类型 | 问题 | 原因 |")
        lines.append("|---|---|---|---|")
        for r in fails[:20]:
            lines.append(f"| {r['id']} | {r['type']} | {r['question'][:30]} | {r['why']} |")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[run_eval] 报告 → {out_path}")
    print(
        f"[run_eval] 总体 {sum(r['ok'] for r in results)}/{len(results)}"
        f"｜执行准确率 {exec_p}/{exec_n}"
    )


def _embed_backend() -> str:
    import yaml

    cfg = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    return cfg.get("models", {}).get("embed", {}).get("backend", "?")


if __name__ == "__main__":
    main()
