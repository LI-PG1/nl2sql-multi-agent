"""运行配置加载：config/settings.yaml（模型角色 / 检索 / 重试 / 澄清参数）。"""

from __future__ import annotations

import functools
import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
SETTINGS_PATH = ROOT / "config" / "settings.yaml"


@functools.lru_cache(maxsize=1)
def load_settings() -> dict:
    return yaml.safe_load(SETTINGS_PATH.read_text(encoding="utf-8"))
