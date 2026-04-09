"""构建索引：Schema（表 / 字段块）+ few-shot。

用法：
    python scripts/build_index.py --backend mock     # 干跑（默认，机制验证）
    python scripts/build_index.py --backend bge      # 真实语义（需 sentence-transformers）
"""

from __future__ import annotations

import argparse
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.config import load_settings  # noqa: E402
from src.embed import get_embedder  # noqa: E402
from src.fewshots import load_fewshots  # noqa: E402
from src.index_store import build  # noqa: E402
from src.linking import INDEX_DIR, load_schema_notes  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "orders.db"


def schema_blocks(db_path: pathlib.Path) -> list[dict]:
    notes = load_schema_notes()
    rows: list[dict] = []
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        tables = [
            r[0]
            for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        ]
        for t in tables:
            cols = [c[1] for c in con.execute(f"PRAGMA table_info({t})")]
            tnote = (notes.get(t) or {}).get("note", "")
            fnotes = (notes.get(t) or {}).get("fields", {})
            parts = [f"{c}（{fnotes[c]}）" if c in fnotes else c for c in cols]
            rows.append(
                {"type": "table", "name": t, "text": f"表 {t}（{tnote}）：{'、'.join(parts)}"}
            )
            for c in cols:
                fn = fnotes.get(c, "")
                text = f"{t}.{c}：{fn}" if fn else f"{t}.{c}（{t} 表字段）"
                rows.append(
                    {
                        "type": "field",
                        "table": t,
                        "field": c,
                        "table_field": f"{t}.{c}",
                        "text": text,
                    }
                )
    finally:
        con.close()
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default=None, help="mock | bge（默认取 settings.yaml）")
    ap.add_argument("--db", default=str(DB_PATH))
    args = ap.parse_args()

    st = load_settings()
    ecfg = st.get("models", {}).get("embed", {})
    backend = args.backend or ecfg.get("backend", "mock")
    emb = get_embedder(backend, ecfg.get("model"), ecfg.get("base_url"))

    db = pathlib.Path(args.db)
    if not db.exists():
        raise SystemExit(f"数据库不存在：{db}（先运行 scripts/build_db.py）")

    blocks = schema_blocks(db)
    idx = build(blocks, emb)
    idx.save(INDEX_DIR, "schema_index")
    n_t = sum(1 for r in blocks if r["type"] == "table")
    n_f = sum(1 for r in blocks if r["type"] == "field")
    print(f"[schema_index] backend={backend} dim={idx.dim} 表={n_t} 字段={n_f} → {INDEX_DIR}")

    fs = load_fewshots()
    if fs:
        fs_rows = [
            {
                "type": "fewshot",
                "id": r["id"],
                "question": r["question"],
                "text": r["question"] + " " + " ".join(r.get("tables", [])),
            }
            for r in fs
        ]
        idx2 = build(fs_rows, emb)
        idx2.save(INDEX_DIR, "fewshot_index")
        print(f"[fewshot_index] 条数={len(fs_rows)} dim={idx2.dim}")
    else:
        print("[fewshot_index] 跳过（config/fewshots.jsonl 为空）")


if __name__ == "__main__":
    main()
