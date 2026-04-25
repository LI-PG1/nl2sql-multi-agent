"""校验多步任务集的标准答案：执行 gold_sql → 缓存 eval/answers/multistep_gold.jsonl。

用法：python eval/validate_multistep_gold.py
退出码：0=全部通过；1=有失败（失败明细见输出）。
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.pipeline import DB_PATH, run_sql  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
TASKS = ROOT / "eval" / "multistep_tasks.jsonl"
CACHE = ROOT / "eval" / "answers" / "multistep_gold.jsonl"


def main() -> None:
    tasks = [
        json.loads(line) for line in TASKS.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    cache, bad = [], 0
    print(f"[validate_multistep_gold] 任务 {len(tasks)} 条")
    for t in tasks:
        vals: list[float] = []
        errs: list[str] = []
        for q in t.get("gold_sql", []):
            try:
                _, rows = run_sql(q, DB_PATH)
                if not rows:
                    errs.append("gold 空结果")
                    continue
                vals.extend(float(c) for r in rows for c in r if isinstance(c, (int, float)))
            except Exception as e:  # noqa: BLE001
                errs.append(f"{type(e).__name__}: {str(e)[:100]}")
        if errs or not vals:
            bad += 1
            print(f"[FAIL] {t['id']:>6s} {t['type']}｜{errs or '无有效数值'}")
        else:
            show = ", ".join(f"{v:g}" for v in vals[:6]) + ("..." if len(vals) > 6 else "")
            print(f"[ OK ] {t['id']:>6s} {t['type']}｜gold={len(vals)} 值：{show}")
        cache.append({"id": t["id"], "values": vals, "errors": errs})

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE, "w", encoding="utf-8") as f:
        for c in cache:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"\n[validate_multistep_gold] {len(tasks) - bad}/{len(tasks)} 通过｜缓存 → {CACHE}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
