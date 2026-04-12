"""few-shot 库管理（骨架版）：加载 + 词面重合 / 表交集检索 + 提示词格式化。

- 升级点（M2 步骤 4）：库扩容至 20-30 条 + 向量检索（fewshot_index）+ 防泄漏校验。
"""

from __future__ import annotations

import json
import pathlib

from . import textutil

ROOT = pathlib.Path(__file__).resolve().parents[1]
FEWSHOTS_PATH = ROOT / "config" / "fewshots.jsonl"


def load_fewshots(path: str | pathlib.Path | None = None) -> list[dict]:
    fp = pathlib.Path(path or FEWSHOTS_PATH)
    if not fp.exists():
        return []
    return [
        json.loads(line) for line in fp.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def select_fewshots(
    question: str, candidate_tables: list[str] | None = None, k: int = 3
) -> list[dict]:
    rows = load_fewshots()
    if not rows:
        return []
    ct = set(candidate_tables or [])
    scored = []
    for r in rows:
        s = textutil.overlap(question, r.get("question", ""))
        if ct and set(r.get("tables", [])) & ct:
            s += 0.2
        scored.append((s, r))
    scored.sort(key=lambda x: -x[0])
    top = [r for s, r in scored[:k] if s > 0]
    return top if top else [scored[0][1]]  # 无重合时回退 top-1，避免空手


def format_examples(rows: list[dict]) -> str:
    if not rows:
        return ""
    blocks = [f"问题：{r['question']}\nSQL：{r['sql']}" for r in rows]
    return "\n\n".join(blocks)
