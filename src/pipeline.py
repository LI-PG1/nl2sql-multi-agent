"""最小主链路原型（M1）：问题 → LLM 生成 SQL → 安全校验 → 执行 → 结果。

M2 将升级为 LangGraph 状态图版本（见 src/graph.py），本文件保留为图节点的基础函数。
"""

from __future__ import annotations

import pathlib
import re
import sqlite3

from .guard import validate_sql

ROOT = pathlib.Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "orders.db"

SYSTEM = (
    "你是数据分析助手，把用户的自然语言问题转成一条 SQLite 查询。"
    "要求：只输出一条 SELECT 语句；只使用给定表与字段；不确定口径时选最合理的一种。"
)

GLOSSARY = """口径提示：
- 销售额默认口径 = order_payments.payment_value 的合计（可按需区分是否仅计入 delivered 订单）；
- 运费 freight_value 在 order_items 中，与商品金额 price 分开；
- 时间字段为 ISO 文本（如 2017-05-16 15:05:36），可用 date()/strftime() 处理；
- 订单状态 order_status 取值如 delivered / shipped / canceled / unavailable。"""


def build_schema_text(db_path: pathlib.Path = DB_PATH) -> str:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        tables = [
            r[0]
            for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        ]
        lines = ["可用表（SQLite）："]
        for t in tables:
            cols = [c[1] for c in con.execute(f"PRAGMA table_info({t})")]
            lines.append(f"- {t}({', '.join(cols)})")
        return "\n".join(lines)
    finally:
        con.close()


def extract_sql(text: str) -> str:
    m = re.search(r"```sql\s*(.*?)```", text, re.S | re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r"(?is)(select\b.*)", text)
    return m.group(1).strip().rstrip(";") if m else ""


def run_sql(sql: str, db_path: pathlib.Path = DB_PATH, max_rows: int = 200):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(max_rows)
        return cols, rows
    finally:
        con.close()


def answer(question: str, llm, db_path: pathlib.Path = DB_PATH) -> dict:
    """返回 {ok: True, sql, columns, rows} 或 {ok: False, stage, error, sql}。"""
    schema = build_schema_text(db_path)
    raw = llm.chat(SYSTEM, f"{schema}\n\n{GLOSSARY}\n\n问题：{question}\n请生成 SQL。")
    sql = extract_sql(raw)
    ok, cleaned_or_err = validate_sql(sql)
    if not ok:
        return {"ok": False, "stage": "guard", "error": cleaned_or_err, "sql": sql}
    try:
        cols, rows = run_sql(cleaned_or_err, db_path)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "stage": "exec", "error": str(e), "sql": cleaned_or_err}
    return {"ok": True, "sql": cleaned_or_err, "columns": cols, "rows": rows}
