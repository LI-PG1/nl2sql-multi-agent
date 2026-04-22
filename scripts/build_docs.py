"""渲染口径文档：config/metrics.yaml + schema_notes.yaml → docs/口径手册.md + docs/数据字典.md。

用途：search_doc 技能的检索源（配合 scripts/build_doc_index.py 入库）。
用法：python scripts/build_docs.py
"""

from __future__ import annotations

import pathlib
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.linking import load_schema_notes  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]


def render_metrics() -> str:
    cfg = yaml.safe_load((ROOT / "config" / "metrics.yaml").read_text(encoding="utf-8"))
    lines = [
        "# 口径手册（指标定义与口径变体）",
        "",
        "> 由 `config/metrics.yaml` 渲染生成（`python scripts/build_docs.py`）；供口径检索与人工查阅。",
        "",
    ]
    for m in cfg.get("metrics", []):
        lines.append(f"## 指标：{m['name']}（{m['id']}）")
        lines.append(f"- 定义：{m['definition']}")
        grp_id = m.get("ambiguity_group")
        if grp_id:
            grp = next(g for g in cfg["ambiguity_groups"] if g["id"] == grp_id)
            lines.append(f"- 关联口径组：{grp['name']}（默认口径：{grp['default_variant']}）")
        lines.append("")
    for g in cfg.get("ambiguity_groups", []):
        lines.append(f"## 口径组：{g['name']}（{g['id']}）")
        lines.append(f"- 触发词：{'、'.join(g['triggers'])}")
        lines.append(f"- 默认口径：{g['default_variant']}")
        lines.append(f"- 澄清追问：{g['clarify_question']}")
        lines.append("- 口径变体：")
        for v in g["variants"]:
            lines.append(f"  - {v['name']}（{v['id']}）：{v['definition']}")
        lines.append("")
    return "\n".join(lines)


def render_dict() -> str:
    notes = load_schema_notes()
    lines = [
        "# 数据字典（表与字段说明）",
        "",
        "> 由 `config/schema_notes.yaml` 渲染生成；Olist 巴西电商库（9 张表）。",
        "",
    ]
    for t, info in notes.items():
        lines.append(f"## 表：{t}")
        lines.append(f"- 说明：{info.get('note', '')}")
        for fld, note in (info.get("fields") or {}).items():
            lines.append(f"  - {fld}：{note}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    (ROOT / "docs" / "口径手册.md").write_text(render_metrics(), encoding="utf-8")
    (ROOT / "docs" / "数据字典.md").write_text(render_dict(), encoding="utf-8")
    print("[build_docs] 已生成 docs/口径手册.md、docs/数据字典.md")


if __name__ == "__main__":
    main()
