"""检索后端对照（索引层）：现役 BGE vs 自部署 Qwen3-Embedding-4B。

口径（公平对照设计）：
- **索引层对照**：用两套后端对同一批索引文本（schema_index 61 条 + doc_index 18 条）重算向量，
  测同一组探针的 Top-3 命中——隔离同义词/规则等上层加权，纯测「嵌入质量」差异：
  · Linking 探针 11 条（来自 scripts/check_linking.py）：期望表全部进 Top-3（表条目内排序）；
  · 口径文档探针 12 条：Top-3 块文本含任一期望关键词（语义定位，非字面命中）。
- 附测：单次查询平均时延（本地 bge vs 公网服务）、语料批量编码耗时。

用法：
    python eval/run_embed_compare.py
输出：
    eval/report_embed_compare.md（含逐条明细与裁决建议）
"""

from __future__ import annotations

import json
import pathlib
import sys
import time

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from check_linking import CASES as LINK_CASES  # noqa: E402  （11 条抽检用例）

from src.embed import get_embedder  # noqa: E402

INDEX_DIR = ROOT / "data" / "index"
REPORT = ROOT / "eval" / "report_embed_compare.md"

# —— 口径文档探针：语义定位（问题里往往不含目标块的原词）——
DOC_CASES: list[dict] = [
    {"q": "客单价的口径怎么定义的？", "keys": ["客单价"]},
    {"q": "订单量是如何统计的？", "keys": ["订单量"]},
    {"q": "配送时长怎么算？", "keys": ["配送时长"]},
    {"q": "客户数去重按哪个字段？", "keys": ["客户数"]},
    {"q": "销售额默认包含哪些金额？", "keys": ["金额范围", "payment_total"]},
    {"q": "取消的订单在统计里怎么处理？", "keys": ["取消订单", "cancel"]},
    {"q": "时间口径默认用哪个字段？", "keys": ["时间口径", "order_purchase_timestamp"]},
    {"q": "订单平均商品金额怎么定义？", "keys": ["订单平均商品金额"]},
    {"q": "支付金额存在哪张表？", "keys": ["order_payments"]},
    {"q": "评分数据在哪个表？", "keys": ["order_reviews"]},
    {"q": "品类的中英文对照在哪里？", "keys": ["category_translation"]},
    {"q": "买家所在城市和州在哪个表？", "keys": ["customers"]},
]


def load_jsonl(name: str) -> list[dict]:
    return [
        json.loads(line)
        for line in (INDEX_DIR / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def topk(scores: np.ndarray, entries: list[dict], k: int) -> list[tuple[dict, float]]:
    idx = np.argsort(-scores)[:k]
    return [(entries[i], float(scores[i])) for i in idx]


def eval_backend(
    backend: str,
    model: str | None,
    base_url: str | None,
    schema_entries: list[dict],
    doc_entries: list[dict],
) -> dict:
    emb = get_embedder(backend, model, base_url)
    t0 = time.perf_counter()
    s_mat = emb.embed([e["text"] for e in schema_entries])
    d_mat = emb.embed([e["text"] for e in doc_entries])
    corpus_s = round(time.perf_counter() - t0, 1)

    table_mask = [i for i, e in enumerate(schema_entries) if e.get("type") == "table"]
    t_names = [schema_entries[i].get("name", "") for i in table_mask]

    link_hits, link_rows = 0, []
    t_link = time.perf_counter()
    for c in LINK_CASES:
        qv = emb.embed([c["q"]])[0]
        scores = s_mat[table_mask] @ qv
        top3 = [t_names[i] for i in np.argsort(-scores)[:3]]
        ok = all(t in top3 for t in c["expect"])
        link_hits += ok
        link_rows.append({"q": c["q"], "expect": c["expect"], "top3": top3, "ok": ok})
    link_ms = (time.perf_counter() - t_link) * 1000 / len(LINK_CASES)

    doc_hits, doc_rows = 0, []
    t_doc = time.perf_counter()
    for c in DOC_CASES:
        qv = emb.embed([c["q"]])[0]
        scores = d_mat @ qv
        top3 = topk(scores, doc_entries, 3)
        ok = any(any(k in e.get("text", "") for k in c["keys"]) for e, _ in top3)
        doc_hits += ok
        doc_rows.append(
            {
                "q": c["q"],
                "keys": c["keys"],
                "top1": (top3[0][0].get("title", "") if top3 else ""),
                "ok": ok,
            }
        )
    doc_ms = (time.perf_counter() - t_doc) * 1000 / len(DOC_CASES)
    avg_ms = (link_ms * len(LINK_CASES) + doc_ms * len(DOC_CASES)) / (
        len(LINK_CASES) + len(DOC_CASES)
    )

    return {
        "backend": backend,
        "dim": int(s_mat.shape[1]),
        "corpus_s": corpus_s,
        "avg_query_ms": round(avg_ms, 1),
        "link_hits": link_hits,
        "link_rows": link_rows,
        "link_n": len(LINK_CASES),
        "doc_hits": doc_hits,
        "doc_rows": doc_rows,
        "doc_n": len(DOC_CASES),
    }


def main() -> None:
    schema_entries = load_jsonl("schema_index")
    doc_entries = load_jsonl("doc_index")
    results = []
    for backend in ("bge", "remote"):
        print(f"[embed_compare] 运行后端：{backend} …", flush=True)
        results.append(eval_backend(backend, None, None, schema_entries, doc_entries))

    bge, r4 = results[0], results[1]
    lines = [
        "# 检索后端对照报告（BGE vs Qwen3-Embedding-4B · 索引层）",
        "",
        f"- 日期：{time.strftime('%Y-%m-%d %H:%M')}｜方法：同语料重算向量 + 同探针 Top-3 命中对照（隔离上层加权）",
        f"- 语料：schema_index {len(schema_entries)} 条（表/字段块）+ doc_index {len(doc_entries)} 条（口径/字典块）",
        f"- 探针：Linking {bge['link_n']} 条（期望表全进 Top-3）+ 口径文档 {bge['doc_n']} 条（Top-3 块含关键词）",
        "",
        "## 总指标",
        "",
        "| 指标 | BGE（现役） | Qwen3-Embedding-4B（自部署） |",
        "|---|---|---|",
        f"| 向量维度 | {bge['dim']} | {r4['dim']} |",
        f"| Linking 探针命中 | {bge['link_hits']}/{bge['link_n']} = {bge['link_hits'] / bge['link_n']:.0%} | "
        f"**{r4['link_hits']}/{r4['link_n']} = {r4['link_hits'] / r4['link_n']:.0%}** |",
        f"| 文档探针命中 | {bge['doc_hits']}/{bge['doc_n']} = {bge['doc_hits'] / bge['doc_n']:.0%} | "
        f"**{r4['doc_hits']}/{bge['doc_n']} = {r4['doc_hits'] / bge['doc_n']:.0%}** |",
        f"| 合计 | {bge['link_hits'] + bge['doc_hits']}/{bge['link_n'] + bge['doc_n']} | "
        f"**{r4['link_hits'] + r4['doc_hits']}/{r4['link_n'] + r4['doc_n']}** |",
        f"| 语料编码耗时 | {bge['corpus_s']} s（本地） | {r4['corpus_s']} s（公网服务） |",
        f"| 单查询平均时延 | {bge['avg_query_ms']} ms（本地） | {r4['avg_query_ms']} ms（公网） |",
        "",
    ]

    for r in results:
        lines += [
            f"## 明细 · {r['backend']}",
            "",
            "### Linking 探针",
            "",
            "| 通过 | 问题 | 期望表 | Top-3 |",
            "|---|---|---|---|",
        ]
        for row in r["link_rows"]:
            lines.append(
                f"| {'√' if row['ok'] else '×'} | {row['q'][:30]} | {','.join(row['expect'])} | "
                f"{', '.join(row['top3'])} |"
            )
        lines += ["", "### 文档探针", "", "| 通过 | 问题 | Top-1 命中块 |", "|---|---|---|"]
        for row in r["doc_rows"]:
            lines.append(f"| {'√' if row['ok'] else '×'} | {row['q'][:30]} | {row['top1'][:34]} |")
        lines.append("")

    b_total = bge["link_hits"] + bge["doc_hits"]
    r_total = r4["link_hits"] + r4["doc_hits"]
    verdict = (
        "**Qwen3-Embedding-4B ≥ BGE → 建议接入**（切换 settings.embed.backend=remote，重建三层索引）"
        if r_total >= b_total
        else "BGE 表现不劣于 4B → 维持现役 BGE（不切换），按实测记录"
    )
    lines += [
        "## 裁决",
        "",
        f"- 合计命中：BGE {b_total} vs 4B {r_total}（共 {bge['link_n'] + bge['doc_n']} 条探针）",
        f"- {verdict}",
        "",
    ]
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"[embed_compare] 报告 → {REPORT}")
    print(f"[embed_compare] BGE {b_total} vs 4B {r_total}（共 {bge['link_n'] + bge['doc_n']}）")


if __name__ == "__main__":
    main()
