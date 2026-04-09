"""轻量文本工具：CJK 二元组 + 英文词元（用于词面重合评分）。"""

from __future__ import annotations

import re

_WORD = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")
_CJK = re.compile(r"[\u4e00-\u9fff]+")


def terms(text: str) -> set[str]:
    t = (text or "").lower()
    out = {m for m in _WORD.findall(t)}
    for run in _CJK.findall(t):
        if len(run) == 1:
            out.add(run)
        else:
            out.update(run[i : i + 2] for i in range(len(run) - 1))
    return out


def overlap(a: str, b: str) -> float:
    """a 的词元被 b 覆盖的比例（0-1）。"""
    ta, tb = terms(a), terms(b)
    if not ta:
        return 0.0
    return len(ta & tb) / len(ta)
