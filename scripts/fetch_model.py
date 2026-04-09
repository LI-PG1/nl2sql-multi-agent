"""下载 BGE 模型到 data/models/<短名>/（逐文件直链下载，绕开 huggingface_hub 下载链路）。

用法：
    python scripts/fetch_model.py                  # 默认 BAAI/bge-small-zh-v1.5
    python scripts/fetch_model.py BAAI/bge-m3      # 指定其它模型

说明：本机实测 huggingface_hub 在该代理环境下部分文件下载为空（JSON 解析失败），
      故采用"API 取清单 → 逐文件直链下载（自动跟随 307 跳转）"的稳方式；幂等（已存在跳过）。
"""

from __future__ import annotations

import json
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
MIRROR = "https://hf-mirror.com"
SKIP = {".gitattributes", "README.md", "pytorch_model.bin"}  # safetensors 优先，避免重复大文件
UA = {"User-Agent": "Mozilla/5.0"}


def _get(url: str, timeout: int = 60):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout)


def fetch(url: str, dest: pathlib.Path) -> None:
    with _get(url, timeout=600) as r, open(dest, "wb") as f:
        total = 0
        while True:
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            total += len(chunk)
    print(f"  OK {dest.name} ({total / 1e6:.2f}MB)")


def main() -> None:
    model = sys.argv[1] if len(sys.argv) > 1 else "BAAI/bge-small-zh-v1.5"
    out = ROOT / "data" / "models" / model.split("/")[-1]
    with _get(f"{MIRROR}/api/models/{model}") as r:
        info = json.load(r)
    files = [s["rfilename"] for s in info["siblings"] if s["rfilename"] not in SKIP]
    print(f"[fetch_model] {model}：{len(files)} 个文件 → {out}")
    for fn in files:
        dest = out / fn
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and dest.stat().st_size > 0:
            print(f"  skip {fn}（已存在）")
            continue
        fetch(f"{MIRROR}/{model}/resolve/main/{fn}", dest)
    print(f"[fetch_model] 完成 → {out}")


if __name__ == "__main__":
    main()
