"""Schema Linking：索引检索（向量 + 词面 + 同义词 + 外键邻接扩展）→ 候选表 / 字段压缩。

- 检索机制：向量相似（mock / bge 后端可切）+ 词面覆盖 + 同义词映射加权；
- 召回保障：候选表间若缺 join 路径，自动补桥接表（外键邻接扩展）——宁多勿漏；
- 验收：scripts/check_linking.py（单表 / 多表 / 口径类 抽检）。
"""

from __future__ import annotations

import functools
import pathlib
import re
import sqlite3
from collections import deque

import yaml

from . import metrics, textutil
from .config import load_settings
from .embed import get_embedder
from .index_store import VectorIndex
from .pipeline import DB_PATH, build_schema_text

ROOT = pathlib.Path(__file__).resolve().parents[1]
INDEX_DIR = ROOT / "data" / "index"
NOTES_PATH = ROOT / "config" / "schema_notes.yaml"

_FK_RE = re.compile(r"外键\s*→\s*([A-Za-z_0-9]+)")


@functools.lru_cache(maxsize=1)
def load_schema_notes() -> dict:
    return yaml.safe_load(NOTES_PATH.read_text(encoding="utf-8"))["tables"]


@functools.lru_cache(maxsize=1)
def fk_graph() -> dict[str, set[str]]:
    """从 schema_notes 的"（外键 → X）"描述解析无向邻接图（桥接表扩展用）。"""
    notes = load_schema_notes()
    g: dict[str, set[str]] = {t: set() for t in notes}
    for t, info in notes.items():
        for desc in (info.get("fields") or {}).values():
            m = _FK_RE.search(str(desc))
            if m and m.group(1) in g:
                g[t].add(m.group(1))
                g[m.group(1)].add(t)
    return g


def _bridges(g: dict[str, set[str]], a: str, b: str, max_hops: int = 3) -> set[str]:
    """BFS：a→b 最短路径的中间表（不含端点）；无路径返回空集。"""
    if a == b or a not in g or b not in g:
        return set()
    q = deque([(a, [a])])
    seen = {a}
    while q:
        node, path = q.popleft()
        if len(path) - 1 >= max_hops:
            continue
        for nb in g[node]:
            if nb == b:
                return set(path[1:])
            if nb not in seen:
                seen.add(nb)
                q.append((nb, path + [nb]))
    return set()


def _expand_bridges(tables: list[str], cap: int = 3) -> list[str]:
    """外键邻接扩展：把连接候选表的桥接表补进候选（召回优先、宁多勿漏）。"""
    g = fk_graph()
    extra: set[str] = set()
    for i in range(len(tables)):
        for j in range(i + 1, len(tables)):
            extra |= _bridges(g, tables[i], tables[j])
    extra -= set(tables)
    return tables + sorted(extra)[:cap]


@functools.lru_cache(maxsize=4)
def _table_columns(db_path: str = str(DB_PATH)) -> dict:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        tables = [
            r[0]
            for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        ]
        return {t: [c[1] for c in con.execute(f"PRAGMA table_info({t})")] for t in tables}
    finally:
        con.close()


def select_candidate_schema(
    question: str, top_tables: int | None = None, top_fields: int | None = None
) -> dict:
    st = load_settings()
    rcfg = st.get("retrieval", {})
    top_tables = top_tables or rcfg.get("schema_top_tables", 3)
    top_fields = top_fields or rcfg.get("schema_top_fields", 20)
    syn_hits = metrics.synonym_hits(question)

    index = VectorIndex.load(INDEX_DIR, "schema_index")
    if index is None:
        return _naive(question, syn_hits)

    ecfg = st.get("models", {}).get("embed", {})
    backend = ecfg.get("backend", "mock")
    qv = get_embedder(backend, ecfg.get("model"), ecfg.get("base_url")).embed([question])[0]
    if qv.shape[0] != index.dim:
        return _naive(question, syn_hits)  # 索引与后端维度不一致 → 退化

    # ① 表候选：向量相似 + 词面覆盖 + 同义词映射表加权
    syn_tables = {m.split(".")[0] for h in syn_hits for m in h.get("maps_to", []) if "." in m}
    t_cands = index.search(qv, k=max(top_tables * 3, 6), where=lambda r: r.get("type") == "table")
    for c in t_cands:
        c["_score"] = 0.5 * c["score"] + 0.5 * textutil.overlap(question, c.get("text", ""))
        if c["name"] in syn_tables:
            c["_score"] += 0.08
    t_cands.sort(key=lambda c: c["_score"], reverse=True)
    tables = [c["name"] for c in t_cands[:top_tables]]
    tables = _expand_bridges(tables)  # 召回保障：补 join 路径桥接表

    # ② 字段候选：限定候选表内 + 同义词命中加权
    syn_fields = {m for h in syn_hits for m in h.get("maps_to", [])}
    f_cands = index.search(
        qv,
        k=max(top_fields * 2, 40),
        where=lambda r: r.get("type") == "field" and r.get("table") in tables,
    )
    for c in f_cands:
        c["_score"] = 0.5 * c["score"] + 0.5 * textutil.overlap(question, c.get("text", ""))
        if c.get("table_field") in syn_fields:
            c["_score"] += 0.1
    f_cands.sort(key=lambda c: c["_score"], reverse=True)
    fields = f_cands[:top_fields]

    return {
        "tables": tables,
        "fields": [
            {"table": f["table"], "field": f["field"], "score": round(f["_score"], 4)}
            for f in fields
        ],
        "synonym_hits": syn_hits,
        "schema_text": _assemble_schema_text(tables, fields),
        "source": "index",
    }


def _assemble_schema_text(tables: list[str], fields: list[dict]) -> str:
    notes = load_schema_notes()
    cols_by_table = _table_columns()
    by_table: dict[str, list[str]] = {}
    for f in fields:
        by_table.setdefault(f["table"], []).append(f["field"])
    lines = []
    for t in tables:
        tnote = (notes.get(t) or {}).get("note", "")
        fnotes = (notes.get(t) or {}).get("fields", {})
        selected = by_table.get(t) or cols_by_table.get(t, [])
        parts = [f"{c}（{fnotes[c]}）" if c in fnotes else c for c in selected]
        lines.append(f"表 {t}（{tnote}）：{'、'.join(parts)}")
    return "\n".join(lines)


def _naive(question: str, syn_hits: list[dict]) -> dict:
    """退化路径：无索引时返回全表 schema（M1 方式 + 表说明）。"""
    notes = load_schema_notes()
    extra = [f"- {t}：{v.get('note', '')}" for t, v in notes.items() if v.get("note")]
    text = build_schema_text()
    if extra:
        text += "\n表说明：\n" + "\n".join(extra)
    return {
        "tables": list(_table_columns().keys()),
        "fields": [],
        "synonym_hits": syn_hits,
        "schema_text": text,
        "source": "naive",
    }
