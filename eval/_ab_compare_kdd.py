"""A/B 对比：KDD DataSpace v1（提示词 v0.1）vs v2（提示词 v0.2 + 重试工程）。

用法：
    python eval/_ab_compare_kdd.py
输出：A/B 链路通过率对比 + 变化题明细 + 失败构成变化。
"""

import json
import pathlib
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_summary(p):
    s = json.loads(p.read_text(encoding="utf-8"))
    return {t["task_id"]: t for t in s["tasks"]}, s


def main() -> None:
    a1, s1 = load_summary(ROOT / "eval" / "archive" / "kdd_eval_summary_v1.json")
    b1, sb1 = load_summary(ROOT / "eval" / "archive" / "kdd_eval_summary_interpret_v1.json")
    a2, s2 = load_summary(ROOT / "eval" / "kdd_eval_summary.json")
    b2, sb2 = load_summary(ROOT / "eval" / "kdd_eval_summary_interpret.json")

    print("## KDD A/B 对比（v1 → v2）")
    print()
    print("| 链路 | v1 | v2 | 变化 |")
    print("|---|---|---|---|")
    d_a = s2["passed_task_count"] - s1["passed_task_count"]
    d_b = sb2["passed_task_count"] - sb1["passed_task_count"]
    print(
        f"| A 单发取数 | {s1['passed_task_count']}/60 ({s1['task_accuracy']:.1%}) | "
        f"{s2['passed_task_count']}/60 ({s2['task_accuracy']:.1%}) | {'+' if d_a >= 0 else ''}{d_a} |"
    )
    print(
        f"| B 取数+解读 | {sb1['passed_task_count']}/60 ({sb1['task_accuracy']:.1%}) | "
        f"{sb2['passed_task_count']}/60 ({sb2['task_accuracy']:.1%}) | {'+' if d_b >= 0 else ''}{d_b} |"
    )
    print()

    # 变化题（B 链路视角）
    up = [t for t in b2 if b2[t]["passed"] and not b1.get(t, {}).get("passed")]
    dn = [t for t in b2 if not b2[t]["passed"] and b1.get(t, {}).get("passed")]
    print(f"B 链路新通过（{len(up)}）: {', '.join(sorted(up))}")
    print(f"B 链路新失败（{len(dn)}）: {', '.join(sorted(dn))}")
    print()

    # 失败构成（A 链路）
    def breakdown(sm):
        fails = [t for t in sm["tasks"] if not t["passed"]]
        return Counter(t.get("error_code") or "unknown" for t in fails)

    c1, c2 = breakdown(s1), breakdown(s2)
    keys = sorted(set(c1) | set(c2))
    print("## 失败构成（A 链路）")
    print()
    print("| error_code | v1 | v2 |")
    print("|---|---|---|")
    for k in keys:
        print(f"| {k} | {c1.get(k, 0)} | {c2.get(k, 0)} |")


if __name__ == "__main__":
    main()
