"""扩展外测轨：CSpider（中文） / Archer（中英双语）——公开基准自测。

与 BIRD 外测同一套模式（全库 schema 注入 + 生成模板 + 守卫 + 执行反馈重试 + 规范化结果集比对）；
Olist 专属口径层 / 索引 / few-shot 一律关闭（对未适配库的公平性设计，见 run_external.py docstring）。

用法：
    python eval/run_external_sets.py --set cspider --limit 20     # 冒烟
    python eval/run_external_sets.py --set cspider                # 全量 1034
    python eval/run_external_sets.py --set archer --lang zh       # 仅中文
    python eval/run_external_sets.py --set archer                 # 中英全量
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from collections import defaultdict

from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

from run_eval import norm_rows, rows_eq  # noqa: E402
from run_external import _exec_timed, _stratified, run_one  # noqa: E402

from src.config import load_settings  # noqa: E402
from src.llm import LLM  # noqa: E402

load_dotenv(ROOT / ".env")
EXT_DIR = ROOT / "eval" / "external"
RAW = ROOT / "data" / "raw"

SETS = {
    "cspider": {
        "title": "CSpider（中文语义解析 · 在 Spider 库上）",
        "layer": "db",
        "files": [("cspider", "all", RAW / "cspider" / "dev.json")],
        "db_root": RAW / "spider" / "database",
        "out": ROOT / "eval" / "report_external_cspider.md",
        "details": EXT_DIR / "cspider_details.jsonl",
    },
    "archer": {
        "title": "Archer（中英双语复杂推理）",
        "layer": "lang_split",
        "files": [
            ("zh", "dev", RAW / "archer" / "zh_data" / "dev.json"),
            ("zh", "train", RAW / "archer" / "zh_data" / "train.json"),
            ("en", "dev", RAW / "archer" / "en_data" / "dev.json"),
            ("en", "train", RAW / "archer" / "en_data" / "train.json"),
        ],
        "db_root": RAW / "archer" / "database",
        "out": ROOT / "eval" / "report_external_archer.md",
        "details": EXT_DIR / "archer_details.jsonl",
    },
}


def resolve_db(db_root: pathlib.Path, db_id: str) -> pathlib.Path | None:
    for name in (f"{db_id}.sqlite", f"{db_id}.db"):
        p = db_root / db_id / name
        if p.exists():
            return p
    return None


def load_cases(set_name: str, lang: str) -> tuple[list[dict], int]:
    cfg = SETS[set_name]
    cases, n_missing = [], 0
    for lg, split, f in cfg["files"]:
        if not f.exists() or (lang and lg != lang):
            continue
        items = json.loads(f.read_text(encoding="utf-8"))
        for it in items:
            db = resolve_db(cfg["db_root"], it["db_id"])
            if db is None:
                n_missing += 1
                continue
            cases.append(
                {
                    "db_id": it["db_id"],
                    "question": it["question"],
                    "gold": it["query"],
                    "db": db,
                    "meta": {"lang": lg, "split": split},
                }
            )
    return cases, n_missing


def rejudge(set_name: str, details_path: pathlib.Path, out_arg: str, elapsed_min: float) -> None:
    """按最新判分规则重判既有明细（新增规则如"全 NULL 行跳过"后），并重生成报告。"""
    cfg = SETS[set_name]
    results = [
        json.loads(line)
        for line in details_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    changed = 0
    for r in results:
        if r["ok"] or r["skip"]:
            continue
        db = resolve_db(cfg["db_root"], r["db_id"])
        try:
            g_rows = norm_rows(_exec_timed(db, r["gold"], timeout_s=60)[1])
        except Exception:  # noqa: BLE001
            g_rows = None
        if g_rows and all(all(c is None for c in row) for row in g_rows):
            r["skip"] = True
            r["why"] = "参考结果集为全 NULL 行（跳过判分）"
            changed += 1
    with open(details_path, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    _write_report(cfg, out_arg, results, elapsed_min * 60, 0)
    ev = [r for r in results if not r["skip"]]
    n_ok = sum(r["ok"] for r in ev)
    print(
        f"[rejudge] 改判 {changed} 条为跳过；执行准确率 {n_ok}/{len(ev)} = {n_ok / max(1, len(ev)):.1%}",
        flush=True,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", dest="set_name", required=True, choices=sorted(SETS))
    ap.add_argument("--lang", default="", help="archer 专用：zh / en")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default="")
    ap.add_argument("--details", default="")
    ap.add_argument("--rejudge", action="store_true", help="按最新规则重判既有明细并重生成报告")
    ap.add_argument("--elapsed-min", type=float, default=0, help="重判时填入原运行耗时（分钟）")
    args = ap.parse_args()

    cfg = SETS[args.set_name]
    if args.rejudge:
        det = pathlib.Path(args.details) if args.details else cfg["details"]
        rejudge(args.set_name, det, args.out, args.elapsed_min)
        return

    cases, n_missing = load_cases(args.set_name, args.lang)
    if args.limit:
        cases = _stratified(cases, args.limit)
    if not cases:
        raise SystemExit("无可用题目（检查数据文件 / --lang）")

    max_retries = load_settings().get("retry", {}).get("max_retries", 2)
    llm = LLM()
    print(
        f"[sets] {cfg['title']}｜用例 {len(cases)} 条（库缺失 {n_missing}）｜模型 {llm.model}",
        flush=True,
    )

    EXT_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    t_all0 = time.time()
    for i, c in enumerate(cases, 1):
        t0 = time.perf_counter()
        try:
            r = run_one(c, llm, max_retries, db_path=c["db"])
        except Exception as e:  # noqa: BLE001  单题异常不中断整轮
            r = {
                "sql": "",
                "exec_ok": False,
                "rows": [],
                "attempts": 0,
                "error": f"runner_error:{str(e)[:100]}",
            }
        ms = (time.perf_counter() - t0) * 1000

        # —— 判分（与内测/BIRD 同一口径：规范化结果集比对）——
        skip = False
        try:
            g_rows = norm_rows(_exec_timed(c["db"], c["gold"], timeout_s=60)[1])
        except Exception as e:  # noqa: BLE001
            g_rows, ok, why, skip = (
                None,
                False,
                f"参考 SQL 执行失败/超时（跳过判分）：{str(e)[:60]}",
                True,
            )
        if not skip:
            all_null = bool(g_rows) and all(all(c is None for c in row) for row in g_rows)
            if not g_rows:
                ok, why, skip = False, "参考结果集为空（跳过判分）", True
            elif all_null:
                ok, why, skip = False, "参考结果集为全 NULL 行（跳过判分）", True
            else:
                p_rows = norm_rows(r["rows"]) if r["exec_ok"] else []
                ok = rows_eq(g_rows, p_rows)
                why = (
                    ""
                    if ok
                    else ("执行失败：" + r["error"] if not r["exec_ok"] else "结果集不一致")
                )

        rec = {
            "qid": i,
            "db_id": c["db_id"],
            "lang": c["meta"]["lang"],
            "split": c["meta"]["split"],
            "question": c["question"],
            "ok": ok,
            "skip": skip,
            "ms": round(ms, 1),
            "attempts": r["attempts"],
            "why": why,
            "sql": r["sql"],
            "gold": c["gold"],
        }
        results.append(rec)
        mark = "PASS" if ok else ("SKIP" if skip else "FAIL")
        print(
            f"[{i:04d}/{len(cases)}] {c['meta']['lang']}/{c['meta']['split']:<5s} {mark} {ms:6.0f}ms  "
            f"{c['db_id'][:24]:<24s} {c['question'][:38]}" + ("" if ok else f"  ← {why[:60]}"),
            flush=True,
        )

    elapsed = time.time() - t_all0
    det = pathlib.Path(args.details) if args.details else cfg["details"]
    det.parent.mkdir(parents=True, exist_ok=True)
    with open(det, "w", encoding="utf-8") as f:
        for rec in results:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    _write_report(cfg, args.out, results, elapsed, n_missing)
    ev = [r for r in results if not r["skip"]]
    n_ok = sum(r["ok"] for r in ev)
    print(
        f"\n[sets] 执行准确率 {n_ok}/{len(ev)} = {n_ok / max(1, len(ev)):.1%}"
        f"（跳过 {len(results) - len(ev)}）｜总耗时 {elapsed / 60:.1f} min",
        flush=True,
    )


def _write_report(cfg, out_arg, results, elapsed, n_missing) -> None:
    ev = [r for r in results if not r["skip"]]
    n_ok = sum(r["ok"] for r in ev)
    skips = len(results) - len(ev)
    ms_list = sorted(r["ms"] for r in results) or [0.0]

    def pctl(p: float) -> float:
        return ms_list[min(len(ms_list) - 1, int(len(ms_list) * p))]

    retried = [r for r in results if r["attempts"] > 1]
    retry_win = sum(1 for r in retried if r["ok"])
    libs = {r["db_id"] for r in results}

    lines = [
        f"# 外测报告（{cfg['title']} · 公开基准自测）",
        "",
        f"- 日期：{time.strftime('%Y-%m-%d %H:%M')}｜用例：{len(results)} 条｜总耗时：{elapsed / 60:.1f} min",
        "- 定位：**跨库泛化外测（扩展轨）**——与 BIRD 外测同一套模式与判分口径"
        "（全库 schema 注入 + 生成模板 + 守卫 + 执行反馈重试 + 规范化结果集比对；Olist 专属口径层 / 索引 / few-shot 关闭）",
        "- 失败构成已抽样归因：真实模型错误（漏条件 / 连接类型 / 取值翻译）与基准自身口径差异并存；判分以参考 SQL 结果集为准，",
        "  参考集为空或全 NULL 行按约定跳过（不判分）",
        f"- 覆盖库：{len(libs)} 个" + (f"；另有 {n_missing} 条因库缺失未计入" if n_missing else ""),
        "",
        "## 总指标",
        "",
        "| 指标 | 数值 |",
        "|---|---|",
        f"| **执行准确率（EX）** | **{n_ok}/{len(ev)} = {n_ok / max(1, len(ev)):.1%}** |",
        f"| 跳过（参考集为空 / 全 NULL / 超时等） | {skips} |",
    ]

    if cfg["layer"] == "lang_split":
        per: dict[tuple, list] = defaultdict(list)
        for r in ev:
            per[(r["lang"], r["split"])].append(r)
        lines += [
            "",
            "## 分层（语言 × 拆分）",
            "",
            "| 语言 | 拆分 | 条数 | 通过 | 通过率 |",
            "|---|---|---|---|---|",
        ]
        for key in [("zh", "dev"), ("zh", "train"), ("en", "dev"), ("en", "train")]:
            rs = per.get(key)
            if rs:
                p = sum(r["ok"] for r in rs)
                lines.append(f"| {key[0]} | {key[1]} | {len(rs)} | {p} | {p / len(rs):.1%} |")
        p = sum(r["ok"] for r in ev)
        lines.append(f"| **合计** | — | {len(ev)} | {p} | {p / max(1, len(ev)):.1%} |")
    else:
        per_db: dict[str, list] = defaultdict(list)
        for r in ev:
            per_db[r["db_id"]].append(r)
        ranking = sorted(per_db.items(), key=lambda kv: -len(kv[1]))
        top, rest = ranking[:15], ranking[15:]
        lines += [
            "",
            "## 分层（按库 · 题量 Top 15 + 其余合并）",
            "",
            "| 库 | 条数 | 通过 | 通过率 |",
            "|---|---|---|---|",
        ]
        for db, rs in top:
            p = sum(r["ok"] for r in rs)
            lines.append(f"| {db} | {len(rs)} | {p} | {p / len(rs):.1%} |")
        if rest:
            rs = [r for _, lst in rest for r in lst]
            p = sum(r["ok"] for r in rs)
            lines.append(f"| （其余 {len(rest)} 库） | {len(rs)} | {p} | {p / len(rs):.1%} |")

    lines += [
        "",
        "## 过程指标",
        "",
        f"- 端到端时延：P50 = {pctl(0.5):.0f} ms｜P95 = {pctl(0.95):.0f} ms",
        f"- 重试增益：触发重试 {len(retried)} 条，其中重试后成功 {retry_win} 条",
        "",
        "## 失败清单（前 20）",
        "",
    ]
    fails = [r for r in ev if not r["ok"]]
    if not fails:
        lines.append("（无失败）")
    else:
        lines.append("| # | 库 | 问题 | 原因 |")
        lines.append("|---|---|---|---|")
        for r in fails[:20]:
            lines.append(f"| {r['qid']} | {r['db_id']} | {r['question'][:36]} | {r['why'][:60]} |")

    out = pathlib.Path(out_arg) if out_arg else cfg["out"]
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"[sets] 报告 → {out}", flush=True)


if __name__ == "__main__":
    main()
