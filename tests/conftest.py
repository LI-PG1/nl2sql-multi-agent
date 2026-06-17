# -*- coding: utf-8 -*-
"""pytest 公共夹具：路径注入。

大部分被测模块是纯函数（SQL 守卫、沙箱、文本工具），不需要数据与模型；
口径相关的用例会读 config/metrics.yaml 与 config/synonyms.yaml。
"""

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def repo_root():
    return ROOT
