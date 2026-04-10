"""提示词加载（config/prompts/，版本化为迭代闭环的一部分）。

- load_prompt(name)：读取模板，去掉文件头的 <!-- version --> 注释；
- render(template, **kw)：把 {{key}} 占位符替换为实际内容。
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROMPTS_DIR = ROOT / "config" / "prompts"

SYSTEM = "你是严格遵循指令的中文数据助手，只依据给定信息作答，不编造数据。"


def load_prompt(name: str) -> str:
    text = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    return re.sub(r"^<!--.*?-->\s*", "", text, count=1, flags=re.S)


def render(template: str, **kw) -> str:
    out = template
    for k, v in kw.items():
        out = out.replace("{{" + k + "}}", str(v))
    return out
