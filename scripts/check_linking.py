"""Schema Linking 召回抽检：验证"涉及的表全部进入候选"（召回优先、宁多勿漏）。

用法：
    python scripts/check_linking.py                  # 用 settings.yaml 默认参数
    python scripts/check_linking.py --top-tables 4   # 临时调参对照（不写配置）

判定：每条 case 的「必须命中表」（expect）应全部出现在候选 tables 中；
输出每条候选明细 + 汇总通过率；未全过时给出调参建议。
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.linking import select_candidate_schema  # noqa: E402

# —— 抽检用例：三类覆盖（单表 / 多表 / 口径类）——
CASES: list[dict] = [
    # 单表
    {"q": "各订单状态的订单量分布？", "expect": ["orders"], "type": "single"},
    {"q": "评分低于 3 分的订单有多少？", "expect": ["order_reviews"], "type": "single"},
    {"q": "各支付方式的支付金额合计是多少？", "expect": ["order_payments"], "type": "single"},
    # 多表
    {
        "q": "2018 年各州的销售额排名前五？",
        "expect": ["orders", "order_payments", "customers"],
        "type": "multi",
    },
    {"q": "各品类商品的商品金额排名前十？", "expect": ["order_items", "products"], "type": "multi"},
    {
        "q": "卖家所在州的订单量分布？",
        "expect": ["sellers", "order_items", "orders"],
        "type": "multi",
    },
    {"q": "平均配送天数最长的五个州？", "expect": ["orders", "customers"], "type": "multi"},
    # 口径类
    {"q": "销售额是多少？", "expect": ["order_payments"], "type": "metric"},
    {"q": "不含运费的销售额是多少？", "expect": ["order_items"], "type": "metric"},
    {"q": "净销售额是多少？", "expect": ["order_payments"], "type": "metric"},
    {"q": "2018 年逐月的销售趋势？", "expect": ["orders", "order_payments"], "type": "metric"},
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--top-tables", type=int, default=None, help="覆盖候选表数量（默认取 settings）"
    )
    args = ap.parse_args()

    n_pass = 0
    print("=" * 76)
    print("Schema Linking 召回抽检（判定：expect 中的表必须全部进候选）")
    print("=" * 76)
    for i, c in enumerate(CASES, 1):
        cs = select_candidate_schema(c["q"], top_tables=args.top_tables)
        cand = list(cs.get("tables") or [])
        miss = [t for t in c["expect"] if t not in cand]
        ok = not miss
        n_pass += ok
        mark = "PASS" if ok else "FAIL"
        print(f"\n[{i:02d}][{mark}] {c['q']}")
        print(f"      [{c['type']}] source={cs.get('source')}｜候选表: {cand}")
        print(f"      必须命中: {c['expect']}" + (f"  ← 缺失: {miss}" if miss else ""))
        fields = cs.get("fields") or []
        if fields:
            top_fields = ", ".join(f"{f['table']}.{f['field']}" for f in fields[:8])
            print(f"      候选字段(前8): {top_fields}")
    print("\n" + "=" * 76)
    if n_pass == len(CASES):
        print(f"汇总：{n_pass}/{len(CASES)} 全部通过")
    else:
        print(
            f"汇总：{n_pass}/{len(CASES)} 未全部通过—— 可先试 `--top-tables 4`；"
            "若仍缺，检查 config/schema_notes.yaml 与 synonyms.yaml 的措辞覆盖"
        )
    print("=" * 76)


if __name__ == "__main__":
    main()
