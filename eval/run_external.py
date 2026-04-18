"""跨库外测轨：BIRD Mini-Dev（公开基准）→ 报告。

定位：验证链路在"未经专门适配"的库上的泛化能力。数据来自公开基准
（BIRD mini-dev，500 题 / 11 库；来源与校验见 docs/数据集说明.md）。

外测模式（与内测 Olist 全链路的差异，均为"对未适配库的公平性"设计）：
- Schema 注入：全库 schema（表 / 字段 / 类型 / PK / 外键标注）——BIRD 11 库均为中小库，
  公开基线与多数实现即全库注入；Olist 专属的索引压缩 / 口径层 / few-shot 在外测中关闭；
- 保留链路要素：生成模板（gen_sql_ext / retry_sql_ext）、SQL 守卫（validate_sql）、
  执行反馈重试（上限同 settings.retry）、结果规范化比对（与内测同一判分函数）；
- 输入：question + evidence（BIRD 官方口径：evidence 为作答所需外部知识提示）。

用法：
    python eval/run_external.py --limit 10    # 冒烟（跨库轮转抽样）
    python eval/run_external.py               # 全量 500
    python eval/run_external.py --db formula_1
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import sys
import time
from collections import defaultdict

from dotenv import load_dotenv

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from run_eval import norm_rows, rows_eq  # noqa: E402  （与内测同一判分口径）

from src.config import load_settings  # noqa: E402
from src.guard import validate_sql  # noqa: E402
from src.llm import LLM  # noqa: E402
from src.pipeline import extract_sql  # noqa: E402
from src.prompts import SYSTEM, load_prompt, render  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

BIRD_DIR = ROOT / "data" / "raw" / "bird_mini_dev"
QUESTIONS = BIRD_DIR / "mini_dev_sqlite.json"
DATABASES = BIRD_DIR / "databases"
EXT_DIR = ROOT / "eval" / "external"

EXEC_CAP = 1000  # 结果集比对行上限（两侧同口径）
DIFF_ORDER = ["simple", "moderate", "challenging"]


def db_file(db_id: str) -> pathlib.Path:
    return DATABASES / db_id / f"{db_id}.sqlite"


def _exec_timed(db_path: pathlib.Path, sql: str, timeout_s: float = 30.0, max_rows: int = EXEC_CAP):
    """带时限的 SQLite 执行（progress handler 中止）——防个别病态长查询挂起整轮。"""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    t0 = time.time()
    con.set_progress_handler(lambda: 1 if time.time() - t0 > timeout_s else 0, 20000)
    try:
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        return cols, cur.fetchmany(max_rows)
    finally:
        con.close()


def _classify(msg: str) -> str:
    low = msg.lower()
    if "no such table" in low:
        return "exec:no_such_table"
    if "no such column" in low:
        return "exec:no_such_column"
    if "syntax error" in low:
        return "exec:syntax"
    if "禁止" in msg or "仅允许" in msg or "解析失败" in msg or "空 SQL" in msg:
        return "guard"
    return "exec:other"


_SCHEMA_CACHE: dict[str, str] = {}


def schema_text_from_path(db_path: pathlib.Path) -> str:
    """任意 SQLite 库的全 schema（含类型 / PK / 外键标注），按路径缓存。"""
    key = str(db_path)
    if key in _SCHEMA_CACHE:
        return _SCHEMA_CACHE[key]
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        tables = [
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            )
        ]
        lines = []
        for t in tables:
            fk = {}
            for r in con.execute(f'PRAGMA foreign_key_list("{t}")'):
                fk[r[3]] = f"{r[2]}.{r[4]}"
            cols = []
            for c in con.execute(f'PRAGMA table_info("{t}")'):
                name, typ, pk = c[1], (c[2] or "").upper(), c[5]
                piece = f"{name} {typ}".strip()
                if pk:
                    piece += " (PK)"
                if name in fk:
                    piece += f"（外键 → {fk[name]}）"
                cols.append(piece)
            lines.append(f"表 {t}：" + "、".join(cols))
        text = "\n".join(lines)
    finally:
        con.close()
    _SCHEMA_CACHE[key] = text
    return text


def schema_text(db_id: str) -> str:
    """BIRD 便捷入口（按 db_id 解析库路径）。"""
    return schema_text_from_path(db_file(db_id))


def run_one(case: dict, llm, max_retries: int, db_path: pathlib.Path | None = None) -> dict:
    """单题：生成 → 守卫 → 执行（失败回喂重试）→ 返回结果。

    db_path 缺省按 BIRD 的 db_id 解析；扩展外测（CSpider / Archer 等）传入目标库路径。
    """
    db_id, question = case["db_id"], case["question"]
    evidence = (case.get("evidence") or "").strip()
    db = pathlib.Path(db_path) if db_path else db_file(db_id)
    schema = schema_text_from_path(db)

    error = error_type = last_sql = ""
    sql, rows, exec_ok, attempts = "", [], False, 0
    for attempt in range(max_retries + 1):
        attempts = attempt + 1
        if attempt == 0:
            prompt = render(
                load_prompt("gen_sql_ext"),
                schema_text=schema,
                evidence=evidence or "（无）",
                question=question,
            )
        else:
            prompt = render(
                load_prompt("retry_sql_ext"),
                schema_text=schema,
                evidence=evidence or "（无）",
                question=question,
                error=error,
                error_type=error_type,
                last_sql=last_sql,
            )
        raw = llm.chat(SYSTEM, prompt, max_tokens=1024)  # 上限防失控生成（SQL 场景 1K 足够）
        sql = extract_sql(raw) or ""
        ok, cleaned_or_err = validate_sql(sql)
        if not ok:
            error, error_type, last_sql = cleaned_or_err, "guard", sql
            continue
        try:
            _, rows = _exec_timed(db, cleaned_or_err, timeout_s=30)
            sql, exec_ok = cleaned_or_err, True
            break
        except Exception as e:  # noqa: BLE001
            error, error_type, last_sql = str(e), _classify(str(e)), sql
            rows = []
    return {
        "sql": sql,
        "exec_ok": exec_ok,
        "rows": rows,
        "attempts": attempts,
        "error": "" if exec_ok else f"{error_type}: {error}",
    }


def _stratified(cases: list, n: int) -> list:
    """跨库轮转抽样：每库先取 1 条，再每库取第 2 条……（冒烟覆盖多个库）。"""
    by_db: dict[str, list] = defaultdict(list)
    for c in cases:
        by_db[c["db_id"]].append(c)
    order = sorted(by_db)
    out, idx = [], 0
    while len(out) < n:
        added = False
        for db in order:
            if idx < len(by_db[db]):
                out.append(by_db[db][idx])
                added = True
                if len(out) >= n:
                    break
        if not added:
            break
        idx += 1
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--db", default="", help="仅跑该库（db_id）")
    ap.add_argument("--out", default="eval/report_external_bird.md")
    ap.add_argument("--details", default="eval/external/bird_details.jsonl")
    args = ap.parse_args()

    cases = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    if args.db:
        cases = [c for c in cases if c["db_id"] == args.db]
    if args.limit:
        cases = _stratified(cases, args.limit)
    if not cases:
        raise SystemExit("无可用题目（检查 --db / 数据文件）")

    max_retries = load_settings().get("retry", {}).get("max_retries", 2)
    llm = LLM()
    print(f"[run_external] BIRD Mini-Dev｜用例 {len(cases)} 条｜模型 {llm.model}")

    EXT_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    t_all0 = time.time()
    for i, c in enumerate(cases, 1):
        t0 = time.perf_counter()
        try:
            r = run_one(c, llm, max_retries)
        except Exception as e:  # noqa: BLE001  单题异常不中断整轮
            r = {
                "sql": "",
                "exec_ok": False,
                "rows": [],
                "attempts": 0,
                "error": f"runner_error:{str(e)[:100]}",
            }
        ms = (time.perf_counter() - t0) * 1000

        # —— 判分（与内测同一口径：规范化结果集比对）——
        skip = False
        try:
            g_rows = norm_rows(_exec_timed(db_file(c["db_id"]), c["SQL"], timeout_s=60)[1])
        except Exception as e:  # noqa: BLE001
            g_rows = None
            ok, why, skip = False, f"参考 SQL 执行失败/超时（跳过判分）：{str(e)[:60]}", True
        if g_rows is None:
            pass
        elif not g_rows:
            ok, why, skip = False, "参考结果集为空（跳过判分）", True
        else:
            p_rows = norm_rows(r["rows"]) if r["exec_ok"] else []
            ok = rows_eq(g_rows, p_rows)
            why = "" if ok else ("执行失败：" + r["error"] if not r["exec_ok"] else "结果集不一致")

        rec = {
            "qid": c["question_id"],
            "db_id": c["db_id"],
            "difficulty": c["difficulty"],
            "question": c["question"],
            "ok": ok,
            "skip": skip,
            "ms": round(ms, 1),
            "attempts": r["attempts"],
            "why": why,
            "sql": r["sql"],
            "gold": c["SQL"],
        }
        results.append(rec)
        mark = "PASS" if ok else ("SKIP" if skip else "FAIL")
        print(
            f"[{i:03d}/{len(cases)}] {c['difficulty'][:5]:<5s} {mark} {ms:6.0f}ms  "
            f"{c['db_id'][:22]:<22s} {c['question'][:42]}" + ("" if ok else f"  ← {why[:66]}")
        )

    elapsed = time.time() - t_all0
    # 明细留档（含模型 SQL 与参考 SQL，供失败归因）
    det_path = pathlib.Path(args.details)
    if not det_path.is_absolute():
        det_path = ROOT / det_path
    det_path.parent.mkdir(parents=True, exist_ok=True)
    with open(det_path, "w", encoding="utf-8") as f:
        for rec in results:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    _write_report(args, results, elapsed)
    ev = [r for r in results if not r["skip"]]
    n_ok = sum(r["ok"] for r in ev)
    print(
        f"\n[run_external] 执行准确率 {n_ok}/{len(ev)} = {n_ok / max(1, len(ev)):.1%}"
        f"（跳过 {len(results) - len(ev)}）｜总耗时 {elapsed / 60:.1f} min"
    )


def _write_report(args, results: list, elapsed: float) -> None:
    ev = [r for r in results if not r["skip"]]
    n_ok = sum(r["ok"] for r in ev)
    skips = len(results) - len(ev)
    ms_list = sorted(r["ms"] for r in results) or [0]

    def pctl(p: float) -> float:
        return ms_list[min(len(ms_list) - 1, int(len(ms_list) * p))]

    retried = [r for r in results if r["attempts"] > 1]
    retry_win = sum(1 for r in retried if r["ok"])

    per_diff = {d: [r for r in ev if r["difficulty"] == d] for d in DIFF_ORDER}
    per_db: dict[str, list] = defaultdict(list)
    for r in ev:
        per_db[r["db_id"]].append(r)

    lines = [
        "# 外测报告（BIRD Mini-Dev · 公开基准自测）",
        "",
        f"- 日期：{time.strftime('%Y-%m-%d %H:%M')}｜用例：{len(results)} 条｜总耗时：{elapsed / 60:.1f} min",
        "- 定位：**跨库泛化外测**（未经专门适配的库）——公开基准上的本地自测数字",
        "- 外测模式：全库 schema 注入（含类型/PK/外键）+ 生成模板 + 守卫 + 执行反馈重试（≤2）+ 规范化结果集比对；"
        "Olist 专属口径层 / 索引压缩 / few-shot 在外测中关闭（公平性设计，见脚本 docstring）",
        f"- 模型：见运行环境（自部署端点，settings/.env）｜结果集比对上限 {EXEC_CAP} 行（两侧同口径）",
        "",
        "## 总指标",
        "",
        "| 指标 | 数值 |",
        "|---|---|",
        f"| **执行准确率（EX）** | **{n_ok}/{len(ev)} = {n_ok / max(1, len(ev)):.1%}** |",
        f"| 跳过（参考集为空等） | {skips} |",
        "",
        "## 分层（难度）",
        "",
        "| 难度 | 条数 | 通过 | 通过率 |",
        "|---|---|---|---|",
    ]
    for d, rs in per_diff.items():
        if rs:
            p = sum(r["ok"] for r in rs)
            lines.append(f"| {d} | {len(rs)} | {p} | {p / len(rs):.1%} |")
    lines += ["", "## 分层（数据库）", "", "| 库 | 条数 | 通过 | 通过率 |", "|---|---|---|---|"]
    for db in sorted(per_db):
        rs = per_db[db]
        p = sum(r["ok"] for r in rs)
        lines.append(f"| {db} | {len(rs)} | {p} | {p / len(rs):.1%} |")
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
        lines.append("| qid | 难度 | 库 | 问题 | 原因 |")
        lines.append("|---|---|---|---|---|")
        for r in fails[:20]:
            lines.append(
                f"| {r['qid']} | {r['difficulty']} | {r['db_id']} | "
                f"{r['question'][:36]} | {r['why'][:60]} |"
            )
    out = pathlib.Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"[run_external] 报告 → {out}")


if __name__ == "__main__":
    main()
