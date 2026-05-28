"""失败用例诊断工具：对指定 id 重跑并打印 golden vs model 的 SQL 与结果集差异。

用法（需 LLM_* 环境变量指向目标模型）:
    python eval/_diag_fails.py            # 用内置清单
    python eval/_diag_fails.py g-014 ...  # 指定 id
"""

from __future__ import annotations

import json
import pathlib
import sys

from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from run_eval import norm_rows, rows_eq  # noqa: E402
from src.graph import run  # noqa: E402
from src.llm import LLM  # noqa: E402
from src.pipeline import DB_PATH, run_sql  # noqa: E402

DEFAULT_FAILS = [
    "g-014",
    "g-016",
    "g-020",
    "g-021",
    "g-022",
    "g-028",
    "g-031",
    "g-035",
    "g-037",
    "g-047",
    "g-052",
]


def main() -> None:
    ids = sys.argv[1:] or DEFAULT_FAILS
    cases = {}
    for line in (ROOT / "eval" / "golden_set.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            cases[d["id"]] = d

    llm = LLM()
    for cid in ids:
        c = cases[cid]
        r = run(c["question"], mode="eval", llm=llm)
        m_sql = (r["final"].get("sql") or "").strip()
        print("=" * 78)
        print(f"[{cid}] {c['question']}")
        print(f"--- golden ---\n{(c['sql'] or '').strip()}")
        print(f"--- model ---\n{m_sql}")
        try:
            _, g = run_sql(c["sql"], DB_PATH)
            _, a = run_sql(m_sql or "SELECT 1 WHERE 0", DB_PATH)
            print(f"--- golden rows ({len(g)}) ---")
            for row in g[:6]:
                print("   ", row)
            print(f"--- model rows ({len(a)}) ---")
            for row in a[:6]:
                print("   ", row)
            print(f"--- equal(容差口径): {rows_eq(norm_rows(g), norm_rows(a))}")
        except Exception as e:  # noqa: BLE001
            print(f"--- 执行错误: {e}")


if __name__ == "__main__":
    main()
