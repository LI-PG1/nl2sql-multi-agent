"""口径层与语义规则：指标识别 / 歧义检测与消解 / 变更类意图检测 / 同义词命中。

配置来源：config/metrics.yaml、config/synonyms.yaml（可迭代资产，代码不动）。
"""

from __future__ import annotations

import functools
import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
METRICS_PATH = ROOT / "config" / "metrics.yaml"
SYNONYMS_PATH = ROOT / "config" / "synonyms.yaml"

# 变更类请求（拒答路径）——v0 词表；迭代期依据评测 refuse 类失分样例扩充
MUTATION_PATTERN = re.compile(
    r"(删掉|删除|清空|清理|drop\s+table|delete\s+from|update\s+\S+\s+set|insert\s+into)",
    re.IGNORECASE,
)

# 名词化用法不构成变更请求：「删除率」「删除量」属只读分析指标（避免误拒答）
MUTATION_NOUN = re.compile(r"(删除|清理)(率|数|量|比例|次数)")


@functools.lru_cache(maxsize=1)
def _metrics() -> dict:
    return yaml.safe_load(METRICS_PATH.read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def _synonyms() -> list[dict]:
    return yaml.safe_load(SYNONYMS_PATH.read_text(encoding="utf-8"))["synonyms"]


def groups() -> list[dict]:
    return _metrics()["ambiguity_groups"]


def get_group(group_id: str) -> dict:
    for g in groups():
        if g["id"] == group_id:
            return g
    raise KeyError(f"未知歧义组：{group_id}")


def detect_metrics(question: str) -> dict:
    """识别问题涉及的指标与歧义组触发。"""
    q = question or ""
    hit_groups = []
    for g in groups():
        matched = [t for t in g["triggers"] if t in q]
        if matched:
            hit_groups.append({"group": g["id"], "name": g["name"], "matched": matched})
    hit_metrics = [m["id"] for m in _metrics()["metrics"] if m["name"] in q]
    return {"groups": hit_groups, "metrics": hit_metrics}


def _variant_keyword_hit(group: dict, text: str) -> dict | None:
    for v in group["variants"]:
        if any(k in text for k in v.get("keywords", [])):
            return v
    return None


def detect_ambiguity(question: str, clarified: str | None = None) -> dict | None:
    """返回需要澄清的歧义组；问题或澄清文本已含消解词则跳过。"""
    q = question or ""
    for g in groups():
        if not any(t in q for t in g["triggers"]):
            continue
        if _variant_keyword_hit(g, q) or (clarified and _variant_keyword_hit(g, clarified)):
            continue
        return g
    return None


def resolve_variant(group: dict, question: str = "", clarified: str | None = None) -> dict:
    """确定实际采用的口径变体：澄清文本优先 → 问题自身 → 默认变体。"""
    for source in (clarified or "", question or ""):
        v = _variant_keyword_hit(group, source)
        if v:
            return v
    default_id = group.get("default_variant")
    for v in group["variants"]:
        if v["id"] == default_id:
            return v
    return group["variants"][0]


def clarify_question(group: dict) -> str:
    return group["clarify_question"]


def build_context(question: str, clarified: str | None = None) -> str:
    """生成口径说明文本（喂给生成与解读提示）。"""
    lines = []
    for g in groups():
        if any(t in (question or "") for t in g["triggers"]):
            v = resolve_variant(g, question, clarified)
            tag = "默认" if v["id"] == g.get("default_variant") else "已指定"
            lines.append(f"- {g['name']}：按「{v['name']}」（{tag}）——{v['definition']}")
    return "\n".join(lines) if lines else "（无特殊口径提示）"


def detect_mutation(question: str) -> str | None:
    """检测变更类意图（拒答路径）：命中则返回命中词。

    「删除率」这类名词化指标属只读分析，先行排除，避免过度拒答。
    """
    q = question or ""
    if MUTATION_NOUN.search(q):
        return None
    m = MUTATION_PATTERN.search(q)
    return m.group(1) if m else None


def synonym_hits(question: str) -> list[dict]:
    q = question or ""
    hits = []
    for item in _synonyms():
        aliases = [s.strip() for s in str(item["term"]).split("/")]
        for a in aliases:
            if a and a in q:
                hits.append(
                    {"term": a, "maps_to": item.get("maps_to", []), "note": item.get("note", "")}
                )
                break
    return hits
