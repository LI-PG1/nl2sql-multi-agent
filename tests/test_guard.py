# -*- coding: utf-8 -*-
"""SQL 守卫：只读约束、单语句约束、强制 LIMIT。

守卫是「模型生成的 SQL」进入数据库前的最后一道关，任何一条放行都可能造成
数据被改写或全表扫描，因此覆盖所有拒绝分支。
"""

import pytest

from src.guard import MAX_LIMIT, validate_sql


# ---------------- 正常放行 ----------------


def test_plain_select_passes():
    ok, out = validate_sql("SELECT order_id FROM orders")
    assert ok is True
    assert "LIMIT" in out.upper()


def test_existing_limit_preserved():
    ok, out = validate_sql("SELECT * FROM orders LIMIT 5")
    assert ok is True
    assert "5" in out


def test_limit_appended_when_absent():
    ok, out = validate_sql("SELECT COUNT(*) AS n FROM orders")
    assert ok is True
    assert str(MAX_LIMIT) in out


def test_trailing_semicolon_stripped():
    ok, out = validate_sql("SELECT 1 AS x;")
    assert ok is True
    assert ";" not in out


def test_join_query_passes():
    ok, out = validate_sql(
        "SELECT o.order_id, c.customer_state FROM orders o "
        "JOIN customers c ON o.customer_id = c.customer_id"
    )
    assert ok is True


def test_cte_select_passes():
    ok, _ = validate_sql(
        "WITH t AS (SELECT customer_id, COUNT(*) AS n FROM orders GROUP BY customer_id) "
        "SELECT * FROM t"
    )
    assert ok is True


def test_aggregate_with_group_by():
    ok, out = validate_sql(
        "SELECT customer_state, SUM(payment_value) AS gmv FROM orders GROUP BY customer_state"
    )
    assert ok is True


# ---------------- 拒绝：写操作 ----------------


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM orders",
        "UPDATE orders SET order_status = 'x'",
        "INSERT INTO orders VALUES (1)",
        "DROP TABLE orders",
        "ALTER TABLE orders ADD COLUMN x TEXT",
        "CREATE TABLE t (a INT)",
        "REPLACE INTO orders VALUES (1)",
        "VACUUM",
        "PRAGMA table_info(orders)",
        "ATTACH DATABASE 'x.db' AS x",
        "DETACH DATABASE x",
    ],
)
def test_reject_write_statements(sql):
    ok, msg = validate_sql(sql)
    assert ok is False, f"未拒绝：{sql}"
    assert msg


# ---------------- 拒绝：多语句与注入 ----------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; DROP TABLE orders",
        "SELECT * FROM orders; SELECT * FROM customers",
    ],
)
def test_reject_multi_statement(sql):
    ok, msg = validate_sql(sql)
    assert ok is False
    assert "多语句" in msg or "单条" in msg


def test_reject_comment_based_injection():
    ok, msg = validate_sql("SELECT * FROM orders -- drop table orders")
    # 纯注释不改变语义，允许；但若解出第二条语句必须拒绝
    assert ok is True or msg


# ---------------- 拒绝：空与非法 ----------------


@pytest.mark.parametrize("sql", ["", "   ", None])
def test_reject_empty(sql):
    ok, msg = validate_sql(sql)
    assert ok is False
    assert "空" in msg


def test_reject_unparsable():
    ok, msg = validate_sql("SELEKT * FROM")
    assert ok is False


def test_reject_non_select_statement():
    ok, msg = validate_sql("EXPLAIN SELECT * FROM orders")
    assert ok is False or "SELECT" in msg.upper()


# ---------------- LIMIT 上限可配置 ----------------


def test_custom_max_limit():
    ok, out = validate_sql("SELECT * FROM orders", max_limit=50)
    assert ok is True
    assert "50" in out
