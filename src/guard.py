"""SQL 安全校验：仅允许单条只读 SELECT，自动补 LIMIT。

用法：
    from src.guard import validate_sql
    ok, cleaned_or_err = validate_sql("SELECT * FROM orders")
"""

from __future__ import annotations

import re

import sqlglot
from sqlglot import exp

MAX_LIMIT = 1000

_RAW_FORBIDDEN = re.compile(
    r"\b(drop|delete|update|insert|alter|attach|detach|pragma|vacuum|create|replace)\b",
    re.IGNORECASE,
)


def validate_sql(sql: str, max_limit: int = MAX_LIMIT) -> tuple[bool, str]:
    """返回 (是否通过, 规范化后的 SQL 或错误信息)。"""
    sql = (sql or "").strip().rstrip(";").strip()
    if not sql:
        return False, "空 SQL"
    if ";" in sql:
        return False, "禁止多语句"
    hit = _RAW_FORBIDDEN.search(sql)
    if hit:
        return False, f"检测到禁止关键字：{hit.group(0)}"

    try:
        statements = [s for s in sqlglot.parse(sql, read="sqlite") if s is not None]
    except Exception as e:  # noqa: BLE001
        return False, f"SQL 解析失败：{e}"

    if len(statements) != 1:
        return False, "仅允许单条语句"
    stmt = statements[0]
    if not isinstance(stmt, exp.Select):
        return False, "仅允许 SELECT 查询"

    if stmt.args.get("limit") is None:
        stmt = stmt.limit(max_limit)
    return True, stmt.sql(dialect="sqlite")


if __name__ == "__main__":
    cases = [
        ("SELECT a FROM t", True),
        ("select * from orders limit 5", True),
        ("SELECT 1; DROP TABLE orders", False),
        ("DELETE FROM orders", False),
        ("PRAGMA table_info(orders)", False),
        ("UPDATE orders SET a=1", False),
    ]
    all_pass = True
    for sql, expect in cases:
        ok, out = validate_sql(sql)
        mark = "PASS" if ok == expect else "FAIL"
        if mark == "FAIL":
            all_pass = False
        print(f"[{mark}] {sql}  ->  {out}")
    print("ALL PASS" if all_pass else "SOME FAILED")
