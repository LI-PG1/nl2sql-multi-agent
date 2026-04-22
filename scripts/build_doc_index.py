"""构建口径文档索引（search_doc 的检索源）：docs/口径手册.md + docs/数据字典.md → data/index/doc_index。

切片：按二级标题（##）分块——一个指标 / 口径组 / 表 一块。
用法：python scripts/build_doc_index.py [--backend bge|mock]
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.config import load_settings  # noqa: E402
from src.embed import get_embedder  # noqa: E402
from src.index_store import build  # noqa: E402
from src.linking import INDEX_DIR  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS = {"口径手册.md": "口径手册", "数据字典.md": "数据字典"}


def split_doc(path: pathlib.Path, source: str) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    blocks = re.split(r"(?m)^## ", text)[1:]  # 跳过前言（# 标题 + 说明）
    chunks: list[dict] = []
    for b in blocks:
        title, _, _body = b.partition("\n")
        chunks.append(
            {"type": "doc", "source": source, "title": title.strip(), "text": "## " + b.strip()}
        )
    return chunks


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default=None, help="mock | bge（默认取 settings.yaml）")
    args = ap.parse_args()

    st = load_settings()
    ecfg = st.get("models", {}).get("embed", {})
    backend = args.backend or ecfg.get("backend", "mock")
    emb = get_embedder(backend, ecfg.get("model"), ecfg.get("base_url"))

    rows: list[dict] = []
    for fn, source in DOCS.items():
        p = ROOT / "docs" / fn
        if not p.exists():
            raise SystemExit(f"缺少文档：{p}（先运行 scripts/build_docs.py）")
        rows.extend(split_doc(p, source))

    idx = build(rows, emb)
    idx.save(INDEX_DIR, "doc_index")
    print(f"[doc_index] backend={backend} dim={idx.dim} chunks={len(rows)} → {INDEX_DIR}")


if __name__ == "__main__":
    main()
