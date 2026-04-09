"""few-shot 库校验：① 内部去重 ② 与黄金集防泄漏比对 ③ SQL 可执行性。

用法：python scripts/check_fewshots.py
纪律：few-shot 与黄金集必须零重叠（防"背题"污染评测数字）。
"""

from __future__ import annotations

import json
import pathlib
import re
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.fewshots import load_fewshots  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "eval" / "golden_set.jsonl"
DB = ROOT / "data" / "orders.db"


def norm_q(q: str) -> str:
    return re.sub(r"\s+", "", re.sub(r"[？?！!。，,、：:；;（）()\[\]\"'“”‘’]", "", q or ""))


def norm_sql(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def main() -> None:
    fs = load_fewshots()
    print(f"[check_fewshots] few-shot 条数：{len(fs)}")
    ok = True

    # ① 内部：id / 问题文本去重
    ids = [r["id"] for r in fs]
    dup_ids = {i for i in ids if ids.count(i) > 1}
    if dup_ids:
        ok = False
        print(f"  FAIL 重复 id：{sorted(dup_ids)}")
    seen: dict[str, str] = {}
    for r in fs:
        k = norm_q(r["question"])
        if k in seen:
            ok = False
            print(f"  FAIL 问题重复：{r['id']} 与 {seen[k]} →「{r['question']}」")
        seen[k] = r["id"]
    sql_seen: dict[str, str] = {}
    for r in fs:
        k = norm_sql(r["sql"])
        if k in sql_seen:
            print(f"  WARN SQL 相同：{r['id']} 与 {sql_seen[k]}（可接受，酌情处理）")
        sql_seen.setdefault(k, r["id"])

    # ② 与黄金集：防泄漏（零重叠纪律）
    if GOLDEN.exists():
        golden = [
            json.loads(line)
            for line in GOLDEN.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        gset = {norm_q(g.get("question", "")) for g in golden}
        leaks = [r["id"] for r in fs if norm_q(r["question"]) in gset]
        if leaks:
            ok = False
            print(f"  FAIL 与黄金集重叠（防泄漏违规）：{leaks}")
        else:
            print(f"  OK 与黄金集（{len(golden)} 条）零重叠")
    else:
        print("  （黄金集尚未建立，跳过防泄漏比对——建好后必须回归本步）")

    # ③ SQL 可执行性（只读执行）
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    bad = []
    for r in fs:
        try:
            con.execute(r["sql"]).fetchall()
        except Exception as e:  # noqa: BLE001
            bad.append((r["id"], str(e)[:100]))
    con.close()
    if bad:
        ok = False
        print(f"  FAIL SQL 执行失败 {len(bad)} 条：")
        for i, e in bad:
            print(f"     {i}: {e}")
    else:
        print(f"  OK 全部 SQL 可执行（{len(fs)} 条）")

    print("[check_fewshots] " + ("全部通过" if ok else "存在问题，请修复"))


if __name__ == "__main__":
    main()
