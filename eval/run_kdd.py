"""KDD DataSpace 公测轨（跨库外测 · 官方评测器判分 Task Accuracy）。

定位：把项目的跨库外测方法学（BIRD / CSpider / Archer 同款）应用到 KDD Cup 2026
Data Agent 赛道官方基准 DataSpace 的 60 个公测参考任务上。数据来自 HF 公开镜像
HKUSTDial/DataSpace（2.68 GiB，SHA256 校验通过）；判分使用官方评测器
（BugMaker-Boyan/DataSpace，MIT，vendored 于 eval/kdd_evaluator/）。

两条链路（同一官方判分口径对照，出两个 Task Accuracy）：
- A 单发取数：全库 schema + 简报证据 → gen_sql_ext → SQL 守卫 → 执行反馈重试（≤2）；
- B 取数+解读两阶段：A 的结果（行文本）→ 解读模板 → 最终答案表 CSV（与项目自身
  interpret/综合阶段同构）；解读输出不可解析时回退 A 的结果。

与既有外测轨的同一性：
- Schema 注入：任务库全量 schema（表 / 字段 / 类型 / PK / 行数）；
- 链路要素：gen_sql_ext / retry_sql_ext 模板 + SQL 守卫 + 执行反馈重试（settings.retry）；
- 无任何针对 DataSpace 的专用口径层 / few-shot / 逐题提示特调（问题后缀仅规定输出表格
  格式与标识符引号规则，属基准作答要求，不泄露答案）。

与既有外测轨的差异（均为任务形态所需，非放宽）：
- 每题一个任务本地工作区：eval/kdd_prepare.py 把 csv / json / sqlite 统一进 task.db，
  doc/*.md 与 doc/*.pdf 全文分块进 doc_chunks 表（可 SQL LIKE 检索），knowledge.md 作为
  evidence 注入（gen_sql_ext 的 {{evidence}} 位）；
- 视频模态本环境不可解析：简报中显式声明，报告按"需视频证据"分层，不静默混算；
- 守卫 LIMIT 上限 1000 → 20000（公测 gold 最大 12962 行；交互场景默认值不变）；
- 预测输出按官方 predictions/task_N/prediction.csv 布局，由官方评测器判分。

用法：
    python eval/run_kdd.py --limit 5      # 冒烟（前 5 题）
    python eval/run_kdd.py                # 全量 60 题（链路 A/B）
    python eval/run_kdd.py --no-interpret # 只跑链路 A
    python eval/run_kdd.py --judge        # 只对既有预测跑官方评测器并出报告
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import pathlib
import re
import sqlite3
import subprocess
import sys
import time

from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import load_settings  # noqa: E402
from src.guard import validate_sql  # noqa: E402
from src.llm import LLM  # noqa: E402
from src.pipeline import extract_sql  # noqa: E402
from src.prompts import SYSTEM, load_prompt, render  # noqa: E402

load_dotenv(ROOT / ".env")

BENCH = ROOT / "data" / "raw" / "kdd_dataspace" / "bench" / "DataSpace-Benchmark"
WORK = ROOT / "eval" / "kdd_work"
PRED = ROOT / "eval" / "kdd_pred"
PRED_B = ROOT / "eval" / "kdd_pred_interpret"
EVALUATOR = ROOT / "eval" / "kdd_evaluator" / "evaluate.py"
DETAILS = ROOT / "eval" / "kdd_details.jsonl"
SUMMARY = ROOT / "eval" / "kdd_eval_summary.json"
SUMMARY_B = ROOT / "eval" / "kdd_eval_summary_interpret.json"
REPORT = ROOT / "eval" / "report_kdd.md"

KDD_MAX_LIMIT = 20000  # 守卫上限（公测 gold 最大 12962 行）
EXEC_CAP = 20000  # 执行取行上限
EXEC_TIMEOUT = 30.0  # 单次执行时限（秒）
INTERPRET_CAP = 45000  # 解读阶段输入序列化上限（字符；超出则该题不进解读阶段）

# 问题后缀：基准作答的格式与标识符规则（不含任何答案信息）
Q_SUFFIX = (
    "\n\n[输出格式要求] 本条查询的结果必须直接构成最终答案表：包含题面要求的全部列与全部行；"
    "数值严格遵循题面要求（四舍五入位数、删除末尾多余的 0、单位、百分比）；"
    "排序遵循题面，题面未要求排序时不限；"
    "表名 / 列名含中文或特殊字符（括号、百分号、空格等）时，SQL 中必须用双引号包裹标识符。"
    "\n[数据来源说明] 本工作区中，部分数据表以文档形式提供（doc/ 下与表同名的 .md 文件），"
    "其全文已分块载入 doc_chunks 表（字段：file / seq / text）。"
    "若题面指向'资料 / 报告 / 档案 / 简报'等文档内容，或题面所述的表在数据库内不存在，"
    "请检索 doc_chunks（如 SELECT text FROM doc_chunks WHERE text LIKE '%关键词%'）取原文后用解读阶段作答。"
)

# 解读阶段提示（与项目自身 interpret 阶段同构：观察 → 最终答案，禁止引入结果外的数字）
INTERPRET_PROMPT = """【任务】下面是数据库查询的结果，请基于它输出最终答案表（CSV 格式）。

[原始问题]
{question}

[查询结果]（{n_rows} 行；制表符分隔，首行为列名，空单元格表示 NULL）
{result_text}

[要求]
- 输出必须直接构成最终答案表：完整覆盖问题要求的全部列与全部行，含表头；
- 数值严格遵循题面要求（四舍五入位数、删除末尾多余的 0、单位、百分比）；排序遵循题面；
- 只允许使用查询结果中出现的数据，禁止引入结果之外的任何数值；结果不足时输出已能确定的最大子集；
- 只输出 CSV 本身，不要输出任何解释或额外文字。

输出格式：```csv
...```
"""


def load_tasks() -> list[dict]:
    ids = json.loads(
        (BENCH.parent.parent / "public_reference_tasks.json").read_text(encoding="utf-8")
    )["task_ids"]
    tasks = []
    for tid in ids:
        q = json.loads((BENCH / "input" / tid / "task.json").read_text(encoding="utf-8"))[
            "question"
        ]
        man = json.loads((WORK / tid / "manifest.json").read_text(encoding="utf-8"))
        tasks.append(
            {
                "task_id": tid,
                "question": q,
                "video": man["modalities"]["video"] > 0,
                "n_tables": len(man["tables"]),
                "n_docs": len(man.get("doc_files", [])),
            }
        )
    return tasks


def schema_text(db_path: pathlib.Path) -> str:
    """任务库全量 schema（表 / 字段 / 类型 / PK / 行数）——与外测同款注入。"""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    lines = []
    try:
        for (t,) in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ):
            info = list(con.execute(f'PRAGMA table_info("{t}")'))
            n = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            cols = []
            for c in info:
                pk = " PRIMARY KEY" if c[5] else ""
                cols.append(f"{c[1]} {c[2] or 'TEXT'}{pk}")
            lines.append(f"表 {t}（{n} 行）: " + ", ".join(cols))
    finally:
        con.close()
    return "\n".join(lines)


def _exec_timed(
    db_path: pathlib.Path, sql: str, timeout_s: float = EXEC_TIMEOUT, max_rows: int = EXEC_CAP
):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    t0 = time.time()
    con.set_progress_handler(lambda: 1 if time.time() - t0 > timeout_s else 0, 20000)
    try:
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        return cols, cur.fetchmany(max_rows)
    finally:
        con.close()


def schema_context(db_path: pathlib.Path) -> dict:
    """库内表→列映射（小写）——用于 DQS 幻觉列名检测（按 SQL 引用的表逐一校验）。"""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    ctx = {"cols_by_table": {}, "tables": set()}
    try:
        for (t,) in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ):
            ctx["tables"].add(t.lower())
            ctx["cols_by_table"][t.lower()] = {
                str(c[1]).lower() for c in con.execute(f'PRAGMA table_info("{t}")')
            }
    finally:
        con.close()
    return ctx


def _doc_hint(brief: str, sql: str) -> str:
    """no_such_table/column 时的定向提示：若引用的名称与文档文件名匹配，给出具体检索 SQL。"""
    doc_names = re.findall(r"- ([\w\u4e00-\u9fff\.\-]+\.(?:md|pdf))（", brief)
    base = (
        "\n提示：该名称不在本库真实表/列中。请核对 [可用表与字段] 只用真实存在的名称重写；"
        "若 knowledge 中提及但库内不存在，其数据通常以下方文档形式提供。"
    )
    if not doc_names:
        return base + "\n（本任务无文档文件可在 doc_chunks 检索）"
    # 从报错 SQL 中找可疑标识符（引号包裹的名字 + FROM/JOIN 后的名字），与文档名做相似匹配
    flat = [t.lower() for t in re.findall(r'"([^"]+)"', sql)]
    flat += [t.lower() for t in re.findall(r"(?:FROM|JOIN)\s+([\w\u4e00-\u9fff_]+)", sql, re.I)]
    hits = []
    for d in doc_names:
        stem = d.rsplit(".", 1)[0].lower()
        if any(t and (stem == t or stem in t or t in stem) for t in flat):
            hits.append(d)
    parts = [base]
    if hits:
        parts.append(
            "**检测到引用名称与以下文档匹配——数据就在文档里**："
            + "、".join(hits)
            + "。请直接改用文档检索，例如："
            f"`SELECT seq, text FROM doc_chunks WHERE file = '{hits[0]}' ORDER BY seq`"
            "（若内容过长可先浏览前几块再定位），然后从文本中提取所需数据；"
            "若需整理成表格，检索出的原文会交给解读阶段处理。"
        )
    else:
        parts.append(
            "本任务可用文档："
            + "、".join(doc_names)
            + "（存于 doc_chunks 表，可按 file 或 LIKE 检索："
            "`SELECT seq, text FROM doc_chunks WHERE file='<文档名>' ORDER BY seq`）。"
        )
    return "\n".join(parts)


def _tables_in_sql(sql: str, ctx: dict) -> set[str] | None:
    """SQL 实际引用的表名集（小写）；解析失败返回 None（→ 跳过 DQS 严格校验）。"""
    try:
        import sqlglot
        from sqlglot import exp

        tree = sqlglot.parse_one(sql, read="sqlite")
        names = set()
        for t in tree.find_all(exp.Table):
            if t.name:
                names.add(t.name.lower())
        return names
    except Exception:  # noqa: BLE001
        return None


def _classify(msg: str) -> str:
    low = msg.lower()
    if "no such table" in low:
        return "exec:no_such_table"
    if "no such column" in low:
        return "exec:no_such_column"
    if "syntax error" in low or "unrecognized token" in low:
        return "exec:syntax"
    if "interrupted" in low:
        return "exec:timeout"
    if "禁止" in msg or "仅允许" in msg or "解析失败" in msg or "空 SQL" in msg:
        return "guard"
    return "exec:other"


def dqs_suspect(sql: str, cols: list, rows: list, ctx: dict) -> list[str]:
    """检测 SQLite 双引号字符串怪癖（DQS）：SQL 中双引号包裹的"幻觉标识符"
    无法解析时被静默当作字符串字面量——症状＝结果某列全为同一常量且该常量
    恰为引号内文本、且该文本不是 SQL 所引用表的真实列名。

    注意必须按「SQL 实际引用的表」校验（同一列名可能存在于库内其他表——
    全局名集合会漏检）。命中即视为软失败（返回疑似 token 列表），触发执行反馈重试。
    """
    if not rows:
        return []
    sql_tables = _tables_in_sql(sql, ctx)
    if sql_tables is None:
        return []  # 解析失败：宁可不报，避免误杀
    valid_cols: set[str] = set()
    valid_tables: set[str] = set()
    # 子查询别名/CTE 等会出现在表名集里但 ctx 无键——跳过未知名，仅校验已知表
    for t in sql_tables:
        valid_tables.add(t)
        valid_cols |= ctx["cols_by_table"].get(t, set())
    suspects: list[str] = []
    for tok in set(re.findall(r'"([^"]+)"', sql)):
        tl = tok.lower()
        if tl in valid_cols or tl in valid_tables:
            continue
        if "%" in tok:  # LIKE 模式类字符串，跳过
            continue
        for j in range(len(cols)):
            vals = [str(r[j]) for r in rows if r[j] is not None]
            if vals and vals[0] == tok and all(v == vals[0] for v in vals):
                suspects.append(tok)
                break
    return suspects


def run_one(case: dict, llm, max_retries: int, ctx: dict) -> dict:
    db_path = WORK / case["task_id"] / "task.db"
    brief = (WORK / case["task_id"] / "brief.md").read_text(encoding="utf-8")
    schema = schema_text(db_path)
    question = case["question"] + Q_SUFFIX

    # KDD 轨专用模板（与 BIRD/CSpider/Archer 外测的 *_ext 模板隔离，互不影响）
    tpl_gen = load_prompt("gen_sql_kdd")
    tpl_retry = load_prompt("retry_sql_kdd")
    sql, err, err_type, attempts = "", "", "", 0
    cols, rows = [], []
    best = None  # best-shot：全败兜底（优先"执行成功且有数据"的最近一次）
    for attempt in range(max_retries + 1):
        attempts = attempt + 1
        if attempt == 0:
            prompt = render(tpl_gen, schema_text=schema, evidence=brief, question=question)
        else:
            prompt = render(
                tpl_retry,
                schema_text=schema,
                evidence=brief,
                question=question,
                error_type=err_type,
                error=err,
                last_sql=sql,
            )
        try:
            raw = llm.chat(SYSTEM, prompt)
        except Exception as e:  # noqa: BLE001
            err, err_type = f"LLM 调用失败：{str(e)[:120]}", "llm"
            sql = ""
            continue
        sql = extract_sql(raw)
        ok, cleaned_or_err = validate_sql(sql, max_limit=KDD_MAX_LIMIT)
        if not ok:
            sql, err, err_type = "", cleaned_or_err, "guard"
            continue
        sql = cleaned_or_err
        try:
            cols, rows = _exec_timed(db_path, sql)
        except Exception as e:  # noqa: BLE001
            err, err_type = str(e)[:200], _classify(str(e))
            if err_type in ("exec:no_such_table", "exec:no_such_column"):
                err += _doc_hint(brief, sql)
            cols, rows = [], []
            continue
        sus = dqs_suspect(sql, cols, rows, ctx)
        if sus:
            err = (
                f"引号标识符被 SQLite 静默当作字符串字面量（疑似幻觉列名）：{'、'.join(sus)}"
                " ——结果列全为该字样常量，说明这些名字不是查询所引用表的真实列。"
                "请对照 [可用表与字段] 使用【所查表自身】的真实列名重写"
                "（注意：同名栏目可能存在于其他表，但必须在当前查询的表内存在）；"
                "若数据在文档中，检索 doc_chunks。"
            )
            err_type = "dqs"
            cols, rows = [], []
            continue
        # 空结果软失败：本题集全部任务 gold 非空（min 1 行）→ 0 行必为错误结果，
        # 回喂重试（过滤条件过严 / 表选错 / 该用文档取值）。
        if not rows and attempt < max_retries:
            best = best or {"sql": sql, "cols": cols, "rows": rows, "err": "", "err_type": ""}
            err = (
                "查询执行成功但结果为空（0 行）。本题的正确答案必然非空——"
                "请检查：① WHERE 过滤条件是否过严（如日期/数值边界、字段取值拼写）；"
                "② 是否选错了表（同义数据可能在另一张表或文档中）；"
                "③ 是否应放宽维度条件（如分年/分地区拆分的表需跨表合并）。请修正后重试。"
            )
            err_type = "empty_result"
            cols, rows = [], []
            continue
        err, err_type = "", ""
        best = {"sql": sql, "cols": cols, "rows": rows, "err": "", "err_type": ""}
        break

    # best-shot 兜底：全败时优先保留"执行成功且有数据"的一次，而非最后一次的空结果
    if best is not None and (not rows) and best["rows"]:
        return {
            "sql": best["sql"],
            "cols": best["cols"],
            "rows": [list(r) for r in best["rows"]],
            "attempts": attempts,
            "error": err,
            "error_type": err_type,
            "schema_chars": len(schema),
            "brief_chars": len(brief),
        }
    return {
        "sql": sql,
        "cols": cols,
        "rows": [list(r) for r in rows],
        "attempts": attempts,
        "error": err,
        "error_type": err_type,
        "schema_chars": len(schema),
        "brief_chars": len(brief),
    }


def _dedup_header(cols: list) -> list:
    """表头去重（重复别名会破坏官方要求的矩形结构）。"""
    seen: dict[str, int] = {}
    out = []
    for h in cols:
        if h in seen:
            seen[h] += 1
            out.append(f"{h}_{seen[h]}")
        else:
            seen[h] = 0
            out.append(h)
    return out


def write_prediction(pred_root: pathlib.Path, task_id: str, cols: list, rows: list) -> pathlib.Path:
    out = pred_root / task_id
    out.mkdir(parents=True, exist_ok=True)
    path = out / "prediction.csv"
    header = _dedup_header([str(c) for c in (cols or ["col_1"])])
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(["" if v is None else v for v in r])
    return path


def serialize_result(cols: list, rows: list, cap: int = INTERPRET_CAP) -> str | None:
    """结果 → 制表符分隔文本；超上限返回 None（该题不进解读阶段）。"""
    head = "\t".join(str(c) for c in cols)
    parts = [head]
    size = len(head)
    for r in rows:
        line = "\t".join("" if v is None else str(v) for v in r)
        size += len(line) + 1
        if size > cap:
            return None
        parts.append(line)
    return "\n".join(parts)


def parse_csv_output(text: str) -> list[list[str]] | None:
    """从解读输出提取 CSV 行（围栏优先，兜底整流解析）；非矩形返回 None。"""
    m = re.search(r"```(?:csv)?\s*\n(.*?)```", text or "", re.S | re.I)
    body = m.group(1).strip() if m else (text or "").strip()
    if not body:
        return None
    try:
        rows = [r for r in csv.reader(io.StringIO(body))]
    except csv.Error:
        return None
    rows = [r for r in rows if any(str(c).strip() for c in r)]
    if not rows:
        return None
    width = len(rows[0])
    if width == 0 or any(len(r) != width for r in rows):
        return None
    return rows


def interpret_one(question: str, cols: list, rows: list, llm) -> dict:
    """解读阶段：查询结果 → 最终答案表 CSV。返回 {ok, cols, rows, note}。"""
    ser = serialize_result(cols, rows)
    if ser is None:
        return {"ok": False, "cols": [], "rows": [], "note": "结果超解读输入上限（跳过解读）"}
    prompt = INTERPRET_PROMPT.format(question=question, n_rows=len(rows), result_text=ser)
    last_err = ""
    for _ in range(2):  # 网络抖动重试一次
        try:
            raw = llm.chat(SYSTEM, prompt)
        except Exception as e:  # noqa: BLE001
            last_err = f"解读调用失败：{str(e)[:80]}"
            continue
        parsed = parse_csv_output(raw)
        if parsed is None:
            return {
                "ok": False,
                "cols": [],
                "rows": [],
                "note": "解读输出不可解析（回退链路 A 结果）",
            }
        return {"ok": True, "cols": parsed[0], "rows": [list(r) for r in parsed[1:]], "note": ""}
    return {"ok": False, "cols": [], "rows": [], "note": last_err or "解读调用失败"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--no-interpret", action="store_true", help="只跑链路 A（单发取数）")
    ap.add_argument("--judge", action="store_true", help="只跑官方评测器 + 生成报告")
    ap.add_argument(
        "--retries", type=int, default=0, help="重试上限（默认取 settings.retry.max_retries）"
    )
    args = ap.parse_args()

    if args.judge:
        sums = {"A 单发取数": judge_and_report(PRED, SUMMARY)}
        if PRED_B.exists() and any(PRED_B.iterdir()):
            sums["B 取数+解读"] = judge_and_report(PRED_B, SUMMARY_B)
        _write_report(sums)
        print(f"[kdd] 报告 → {REPORT}", flush=True)
        return

    tasks = load_tasks()
    if args.offset:
        tasks = tasks[args.offset :]
    if args.limit:
        tasks = tasks[: args.limit]
    max_retries = (
        args.retries if args.retries > 0 else load_settings().get("retry", {}).get("max_retries", 2)
    )
    llm = LLM()
    do_interp = not args.no_interpret
    print(
        f"[kdd] 公测任务 {len(tasks)} 条｜模型 {llm.model}｜重试上限 {max_retries}"
        f"｜守卫 LIMIT {KDD_MAX_LIMIT}｜解读阶段 {'开' if do_interp else '关'}",
        flush=True,
    )

    PRED.mkdir(parents=True, exist_ok=True)
    DETAILS.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if (args.limit or args.offset) else "w"  # 全量跑重置明细；分段跑追加
    t_all0 = time.time()
    with open(DETAILS, mode, encoding="utf-8") as det:
        for i, c in enumerate(tasks, 1):
            t0 = time.perf_counter()
            try:
                ctx = schema_context(WORK / c["task_id"] / "task.db")
                r = run_one(c, llm, max_retries, ctx)
            except Exception as e:  # noqa: BLE001  单题异常不中断整轮
                r = {
                    "sql": "",
                    "cols": [],
                    "rows": [],
                    "attempts": 0,
                    "error": f"runner_error:{str(e)[:120]}",
                    "error_type": "runner",
                    "schema_chars": 0,
                    "brief_chars": 0,
                }
            ms = (time.perf_counter() - t0) * 1000
            write_prediction(PRED, c["task_id"], r["cols"], r["rows"])

            interp = {"ok": False, "cols": [], "rows": [], "note": "未运行"}
            if do_interp and r["rows"]:
                t1 = time.perf_counter()
                interp = interpret_one(c["question"], r["cols"], r["rows"], llm)
                interp["ms"] = round((time.perf_counter() - t1) * 1000, 1)
            if interp["ok"] and interp["rows"]:
                write_prediction(PRED_B, c["task_id"], interp["cols"], interp["rows"])
                b_tag = f"B:{len(interp['rows'])}行"
            else:
                write_prediction(PRED_B, c["task_id"], r["cols"], r["rows"])  # 回退 A
                b_tag = "B:回退A"
            interp["ms"] = interp.get("ms", 0)

            rec = {
                "task_id": c["task_id"],
                "video": c["video"],
                "n_tables": c["n_tables"],
                "n_docs": c.get("n_docs", 0),
                "rows_n": len(r["rows"]),
                "attempts": r["attempts"],
                "error_type": r["error_type"],
                "error": r["error"][:160],
                "sql_head": r["sql"][:200],
                "ms": round(ms, 1),
                "interp_ok": interp["ok"],
                "interp_note": interp["note"],
                "interp_ms": interp["ms"],
                "interp_rows": len(interp["rows"]),
                "schema_chars": r["schema_chars"],
                "brief_chars": r["brief_chars"],
                "question": c["question"][:120],
            }
            det.write(json.dumps(rec, ensure_ascii=False) + "\n")
            det.flush()
            mark = (
                "OK"
                if (r["rows"] and not r["error"])
                else ("RETRY-FAIL" if r["error"] else "EMPTY")
            )
            print(
                f"[{i:02d}/{len(tasks)}] {c['task_id']} {mark:<10s} A:{len(r['rows']):>5d}行 "
                f"{b_tag:<8s} {ms:6.0f}ms 尝试{r['attempts']} "
                f"{'[视频]' if c['video'] else ''}{c['question'][:36]}"
                + ("" if not r["error"] else f"  ← {r['error_type']}: {r['error'][:50]}"),
                flush=True,
            )

    elapsed = time.time() - t_all0
    print(f"\n[kdd] 作答完成｜耗时 {elapsed / 60:.1f} min｜预测 → {PRED} / {PRED_B}", flush=True)
    sums = {"A 单发取数": judge_and_report(PRED, SUMMARY)}
    if do_interp:
        sums["B 取数+解读"] = judge_and_report(PRED_B, SUMMARY_B)
    _write_report(sums)
    print(f"[kdd] 报告 → {REPORT}", flush=True)


def judge_and_report(pred_dir: pathlib.Path, summary_path: pathlib.Path) -> dict:
    if not pred_dir.exists() or not any(pred_dir.iterdir()):
        raise SystemExit(f"无预测文件：{pred_dir}（先不带 --judge 运行）")
    cmd = [
        sys.executable,
        str(EVALUATOR),
        "--prediction-root",
        str(pred_dir),
        "--gold-root",
        str(BENCH / "output"),
        "--config-root",
        str(BENCH / "evaluation" / "configs"),
        "--output",
        str(summary_path),
    ]
    print(f"[kdd] 官方评测器判分（{pred_dir.name}）...", flush=True)
    out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if out.returncode != 0:
        print(out.stdout[-2000:])
        print(out.stderr[-2000:])
        raise SystemExit(f"官方评测器失败（exit {out.returncode}）")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    n_ok = summary["passed_task_count"]
    print(
        f"[kdd] {pred_dir.name} Task Accuracy {n_ok}/{summary['task_count']}"
        f" = {summary['task_accuracy']:.1%}",
        flush=True,
    )
    return summary


def _write_report(summaries: dict[str, dict]) -> None:
    """summaries: {'A 单发取数': summary_json, 'B 取数+解读': summary_json(可选)}"""
    names = list(summaries.keys())
    key = names[-1]  # 主口径 = 最后一条链路（B / 或仅 A）
    base = summaries[key]
    tasks_main = base.get("tasks", [])  # 失败清单与逐题表以主口径为准
    tasks_a = summaries[names[0]].get("tasks", [])
    details = {}
    if DETAILS.exists():
        for line in DETAILS.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                details[r["task_id"]] = r
    n = len(tasks_a)

    def pass_map(summary: dict) -> dict[str, bool]:
        return {t["task_id"]: t["passed"] for t in summary.get("tasks", [])}

    passed = {name: pass_map(s) for name, s in summaries.items()}

    def subset(pred) -> list[dict]:
        return [t for t in tasks_a if pred(t)]

    groups = {
        "全部公测任务": subset(lambda t: True),
        "无需视频证据": subset(lambda t: not details.get(t["task_id"], {}).get("video", False)),
        "需视频证据（本环境不可解析）": subset(
            lambda t: details.get(t["task_id"], {}).get("video", False)
        ),
        "顺序敏感": subset(lambda t: t.get("order_sensitive")),
        "顺序不敏感": subset(lambda t: not t.get("order_sensitive")),
    }

    lines = [
        "# KDD DataSpace 公测报告（跨库外测 · 官方评测器判分）",
        "",
        f"- 日期：{time.strftime('%Y-%m-%d %H:%M')}｜任务：{n} 条（DataSpace 公测参考包）",
        "- 基准：KDD Cup 2026 Data Agent Track 官方基准 DataSpace（HF 镜像 HKUSTDial/DataSpace，"
        "SHA256 校验通过；410 题输入 + 60 公测参考包）",
        "- 链路 A（单发取数）：全库 schema 注入 + gen_sql_kdd（v0.2 解题纪律：题型三分类 / 列选择 / "
        "字段语义映射 / SQL 陷阱 / 行数自检 + 文档定向提示）+ SQL 守卫 + 执行反馈重试（含空结果软失败）；"
        "链路 B（取数+解读）：A 的结果 → 解读阶段 → 最终答案表 CSV"
        "（与项目自身 interpret 阶段同构；解读不可解析则回退 A）",
        "- 任务形态适配（非口径放宽）：任务本地工作区统一入库；doc/*.md 与 doc/*.pdf 全文分块入 "
        "`doc_chunks` 表（可 SQL LIKE 检索）；knowledge.md 作 evidence；守卫 LIMIT 1000→20000"
        "（gold 最大 12962 行）",
        "- 判分：**官方评测器**（BugMaker-Boyan/DataSpace，vendored 于 eval/kdd_evaluator/；"
        "列一对一对齐 + 类型感知值比对 + 重复行多重性 + 顺序敏感项按序）——Task Accuracy 二值判定，"
        "结果格式（数值精度/尾零）由评测器按各题配置归一化",
        "- 边界披露：视频模态本环境不可解析（28/60 题工作区含视频）；PDF 仅抽取前 10 页文本；"
        "knowledge.md 截断至 16000 字符",
        "",
        "## 总指标（Task Accuracy，官方评测器）",
        "",
        "| 分层 | " + " | ".join(f"{name} 通过/总数" for name in names) + " |",
        "|---|" + "---|" * len(names),
    ]
    for gname, ts in groups.items():
        cells = []
        for name in names:
            p = sum(1 for t in ts if passed[name].get(t["task_id"], False))
            cells.append(f"{p}/{len(ts)}（{p / max(1, len(ts)):.1%}）")
        lines.append(f"| {gname} | " + " | ".join(cells) + " |")

    # 失败清单（以主口径为准）
    main_by_id = {t["task_id"]: t for t in tasks_main}
    fails = [main_by_id[t["task_id"]] for t in tasks_a if not passed[key].get(t["task_id"], False)]
    lines += [
        "",
        f"## 失败清单（{len(fails)} 条，链路「{key}」角度；含官方错误码与链路诊断）",
        "",
        "| 任务 | 需视频 | gold 行 | 预测行 | 官方错误码 | 链路诊断 |",
        "|---|---|---|---|---|---|",
    ]
    for t in fails:
        d = details.get(t["task_id"], {})
        diag = ""
        if d.get("interp_ok"):
            diag = f"解读后 {d.get('interp_rows', '?')} 行"
        if d.get("error"):
            diag = (d.get("error_type", "") + ": " + d.get("error", ""))[:70]
        elif d.get("rows_n") == 0:
            diag = "查询结果为空"
        elif not d.get("interp_ok") and d.get("interp_note"):
            diag = d["interp_note"][:60]
        lines.append(
            f"| {t['task_id']} | {'是' if d.get('video') else '否'} | "
            f"{t.get('gold_row_count', '?')} | {t.get('prediction_row_count', '?')} | "
            f"{t.get('error_code') or 'relation_mismatch'} | {diag} |"
        )

    lines += [
        "",
        "## 逐题结果",
        "",
        "| 任务 | 需视频 | A | B | gold 行 | A 预测行 | 解读 | 尝试 | 耗时 ms |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    a_by_id = {t["task_id"]: t for t in tasks_a}
    for t in tasks_main:
        tid = t["task_id"]
        d = details.get(tid, {})
        ta = a_by_id.get(tid, {})
        a_ok = "√" if passed[names[0]].get(tid, False) else "×"
        b_cell = "—"
        if len(names) > 1:
            b_cell = "√" if passed[names[1]].get(tid, False) else "×"
        interp = "—"
        if d.get("interp_ok"):
            interp = f"{d.get('interp_rows', '?')}行"
        elif d.get("interp_note") and d["interp_note"] != "未运行":
            interp = "回退"
        lines.append(
            f"| {tid} | {'是' if d.get('video') else '否'} | {a_ok} | {b_cell} | "
            f"{t.get('gold_row_count', '?')} | {ta.get('prediction_row_count', '?')} | "
            f"{interp} | {d.get('attempts', '?')} | {d.get('ms', '?')} |"
        )

    lines += [
        "",
        "## 复现方式",
        "",
        "```bash",
        "python eval/kdd_prepare.py      # 60 个工作区 → 任务库 + doc_chunks + 简报（已随仓库数据就绪可跳过）",
        "python eval/run_kdd.py --limit 5   # 冒烟",
        "python eval/run_kdd.py             # 全量 60 题（链路 A/B）",
        "python eval/run_kdd.py --judge     # 官方评测器判分 + 本报告",
        "```",
        "",
        "数据与评测器来源：HF 镜像 HKUSTDial/DataSpace（MIT）；官方评测器 "
        "github.com/BugMaker-Boyan/DataSpace（MIT）。",
    ]
    REPORT.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
