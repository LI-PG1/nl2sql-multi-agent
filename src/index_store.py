"""向量索引存取（轻量 numpy 实现；接口预留 Chroma / Milvus 替换位）。

- 持久化：data/index/<name>.npz（向量）+ <name>.jsonl（元数据，顺序对齐）；
- 检索：余弦相似（向量已归一化，即点积）。
"""

from __future__ import annotations

import json
import pathlib

import numpy as np


class VectorIndex:
    def __init__(self, vectors: np.ndarray, rows: list[dict]):
        self.vectors = vectors
        self.rows = rows

    @property
    def dim(self) -> int:
        return int(self.vectors.shape[1]) if len(self.rows) else 0

    def search(self, qvec: np.ndarray, k: int = 5, where=None) -> list[dict]:
        if not len(self.rows):
            return []
        scores = self.vectors @ qvec
        order = np.argsort(-scores)
        out: list[dict] = []
        for i in order:
            row = self.rows[int(i)]
            if where is not None and not where(row):
                continue
            out.append({**row, "score": float(scores[int(i)])})
            if len(out) >= k:
                break
        return out

    def save(self, directory: str | pathlib.Path, name: str) -> None:
        directory = pathlib.Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(directory / f"{name}.npz", vectors=self.vectors)
        with open(directory / f"{name}.jsonl", "w", encoding="utf-8") as f:
            for r in self.rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    @classmethod
    def load(cls, directory: str | pathlib.Path, name: str) -> "VectorIndex | None":
        directory = pathlib.Path(directory)
        vec_fp = directory / f"{name}.npz"
        row_fp = directory / f"{name}.jsonl"
        if not (vec_fp.exists() and row_fp.exists()):
            return None
        vectors = np.load(vec_fp)["vectors"]
        rows = [
            json.loads(line)
            for line in row_fp.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return cls(vectors, rows)


def build(rows: list[dict], embedder, text_key: str = "text") -> VectorIndex:
    vectors = embedder.embed([r.get(text_key, "") for r in rows])
    return VectorIndex(vectors, rows)
