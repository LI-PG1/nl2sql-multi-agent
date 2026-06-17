# -*- coding: utf-8 -*-
"""口径体系：歧义识别、变更类拒答、同义词映射。

口径歧义是业务取数最常见的错误来源（同名异义），这部分逻辑决定
「澄清」与「直接执行」的分流，错了会导致答非所问或过度追问。
"""

import pytest

from src import metrics


# ---------------- 配置可用性 ----------------


def test_configs_load():
    assert metrics.groups()
    assert metrics.detect_metrics("2018 年销售额")


def test_group_lookup():
    g = metrics.get_group("amount_scope")
    assert g["triggers"]
    assert g["variants"]


def test_unknown_group_raises():
    with pytest.raises(KeyError):
        metrics.get_group("不存在的组")


# ---------------- 指标识别 ----------------


def test_detect_metrics_hits_group():
    res = metrics.detect_metrics("按州统计销售额")
    ids = [g["group"] for g in res["groups"]]
    assert "amount_scope" in ids


def test_detect_metrics_no_hit():
    res = metrics.detect_metrics("今天天气怎么样")
    assert res["groups"] == []


# ---------------- 歧义判定与澄清 ----------------


def test_ambiguity_detected_without_clarification():
    res = metrics.detect_ambiguity("2018 年各州的销售额排名")
    assert res is not None
    assert res["id"] == "amount_scope"


def test_ambiguity_resolved_by_clarification():
    """澄清文本命中某个口径变体的关键词后，不再追问。"""
    res = metrics.detect_ambiguity("2018 年各州的销售额排名", clarified="只要商品金额，不含运费")
    assert res is None


def test_clarification_question_non_empty():
    g = metrics.get_group("amount_scope")
    q = metrics.clarify_question(g)
    assert q and len(q) >= 5


def test_resolve_variant_by_keyword():
    g = metrics.get_group("amount_scope")
    v = metrics.resolve_variant(g, "只要商品金额，不算运费")
    assert v["id"] in [x["id"] for x in g["variants"]]


def test_resolve_variant_default_when_no_hint():
    g = metrics.get_group("amount_scope")
    v = metrics.resolve_variant(g, "销售额是多少")
    assert v["id"] == g.get("default", v["id"])


# ---------------- 变更类意图（拒答路径）----------------


@pytest.mark.parametrize(
    "q,expect_kw",
    [
        ("把订单表删掉", "删"),
        ("清空 customers 表", "清空"),
        ("drop table orders", "drop"),
        ("删除 2018 年的数据", "删除"),
    ],
)
def test_detect_mutation_hits(q, expect_kw):
    hit = metrics.detect_mutation(q)
    assert hit is not None
    assert expect_kw.lower() in hit.lower()


@pytest.mark.parametrize(
    "q",
    [
        "2018 年各州销售额",
        "删除率是多少",
        "有多少被取消的订单",
    ],
)
def test_detect_mutation_misses_readonly(q):
    assert metrics.detect_mutation(q) is None


# ---------------- 同义词映射 ----------------


def test_synonym_expansion():
    hits = metrics.synonym_hits("我想看州的数据")
    assert isinstance(hits, list)
    for h in hits:
        assert "term" in h


def test_synonym_no_hit_on_unrelated():
    assert metrics.synonym_hits("完全无关的一句话") == []
