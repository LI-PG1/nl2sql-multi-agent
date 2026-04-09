"""Embedding 抽象：BGE（真实，懒加载）/ Mock（干跑、单测）。

- BgeEmbedder：sentence-transformers 后端，默认 BAAI/bge-small-zh-v1.5（可换 bge-m3）；
  **本地优先**：data/models/<短名>/ 存在则直接加载（由 scripts/fetch_model.py 下载）；
- MockEmbedder：确定性哈希向量（256 维）——仅用于验证链路机制，不代表真实语义质量。
"""

from __future__ import annotations

import functools
import hashlib
import os
import pathlib

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]

try:  # 独立入口（如 check_linking / 构建脚本）也能读到 .env 中的 EMBED_BASE_URL
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:  # noqa: BLE001
    pass


class Embedder:
    def embed(self, texts: list[str]) -> np.ndarray:  # pragma: no cover
        raise NotImplementedError


class MockEmbedder(Embedder):
    dim = 256

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for gram in _bigrams(t):
                h = int.from_bytes(hashlib.md5(gram.encode("utf-8")).digest()[:4], "little")
                out[i, h % self.dim] += 1.0
            n = float(np.linalg.norm(out[i]))
            if n > 0:
                out[i] /= n
        return out


def _local_model_dir(model_name: str) -> str | None:
    """本地模型目录解析：① 显式路径存在 → 用；② data/models/<短名>/ → 用。"""
    p = pathlib.Path(model_name)
    if p.exists():
        return str(p)
    cand = ROOT / "data" / "models" / model_name.split("/")[-1]
    return str(cand) if cand.exists() else None


class BgeEmbedder(Embedder):
    def __init__(self, model_name: str = "BAAI/bge-small-zh-v1.5"):
        # 本机实测：HF 官方直连不通、走镜像；xet 传输在代理环境下不稳定 → 关闭
        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
        from sentence_transformers import SentenceTransformer  # 懒加载（未安装不影响干跑）

        local = _local_model_dir(model_name)
        self.model = SentenceTransformer(local or model_name)

    def embed(self, texts: list[str]) -> np.ndarray:
        vecs = self.model.encode(texts, normalize_embeddings=True)
        return np.asarray(vecs, dtype=np.float32)


class RemoteEmbedder(Embedder):
    """OpenAI 兼容 /v1/embeddings 远端服务（默认：自部署 Qwen3-Embedding-4B）。

    - base_url 优先级：显式参数 > 环境变量 EMBED_BASE_URL > 默认公网端点；
    - 服务端输出已 L2 归一化（fp32），此处统一转 float32 numpy。
    """

    def __init__(
        self, base_url: str | None = None, model: str = "qwen3-embedding-4b", timeout: float = 30.0
    ):
        base = base_url or os.getenv("EMBED_BASE_URL")
        if not base:
            raise RuntimeError(
                "未配置向量服务端点：请设置 settings.embed.base_url 或环境变量 EMBED_BASE_URL"
                "（或把 embed.backend 切回 bge）"
            )
        self.base_url = base.rstrip("/")
        self.model = model
        self.timeout = timeout

    def embed(self, texts: list[str]) -> np.ndarray:
        import json as _json
        import urllib.request

        payload = _json.dumps({"model": self.model, "input": texts}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/v1/embeddings",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
        vecs = [d["embedding"] for d in data["data"]]
        return np.asarray(vecs, dtype=np.float32)


def _bigrams(text: str) -> list[str]:
    t = (text or "").strip()
    if not t:
        return []
    if len(t) == 1:
        return [t]
    return [t[i : i + 2] for i in range(len(t) - 1)]


@functools.lru_cache(maxsize=8)
def get_embedder(
    backend: str = "mock", model: str | None = None, base_url: str | None = None
) -> Embedder:
    """带缓存的工厂：同一后端只加载一次模型（避免每次查询重复加载）。"""
    if backend == "bge":
        return BgeEmbedder(model or "BAAI/bge-small-zh-v1.5")
    if backend == "remote":
        return RemoteEmbedder(base_url=base_url, model=model or "qwen3-embedding-4b")
    return MockEmbedder()
