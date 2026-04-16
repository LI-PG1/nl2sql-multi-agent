"""黄金集自检：① 参考 SQL 全部可执行 ② 均返回非空结果 ③ 生成标准答案缓存。

用法：python eval/validate_golden.py
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.pipeline import DB_PATH, run_sql  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "eval" / "golden_set.jsonl"
ANS = ROOT / "eval" / "answers" / "golden_results.jsonl"


def main() -> None:
    cases = [
        json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    ANS.parent.mkdir(parents=True, exist_ok=True)
    bad, cache = [], []
    for c in cases:
        if c["type"] == "refuse":
            continue
        try:
            _, rows = run_sql(c["sql"], DB_PATH)
            if not rows:
                bad.append((c["id"], "空结果"))
            cache.append({"id": c["id"], "rows": rows})
        except Exception as e:  # noqa: BLE001
            bad.append((c["id"], str(e)[:80]))
    with open(ANS, "w", encoding="utf-8") as f:
        for d in cache:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    n_refuse = sum(1 for c in cases if c["type"] == "refuse")
    print(
        f"[validate_golden] 用例 {len(cases)} 条（拒答 {n_refuse} 条免执行；答案缓存 {len(cache)} 条）"
    )
    if bad:
        print(f"  FAIL {len(bad)} 条：")
        for i, e in bad:
            print(f"   {i}: {e}")
    else:
        print(f"  OK 全部参考 SQL 可执行且非空 → 缓存写入 {ANS}")
    print("[validate_golden] " + ("全部通过" if not bad else "存在问题，请修复"))


if __name__ == "__main__":
    main()
