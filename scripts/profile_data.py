"""数据画像：扫描 data/raw/olist/*.csv，输出规模 / 字段 / 空值 / 样例 到 data/profile.md。"""

from __future__ import annotations

import pathlib

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "olist"
OUT = ROOT / "data" / "profile.md"


def main() -> None:
    lines = ["# Olist 数据画像（自动生成，脚本：scripts/profile_data.py）", ""]
    for f in sorted(RAW.glob("*.csv")):
        df = pd.read_csv(f, low_memory=False)
        lines.append(f"## {f.name}")
        lines.append(f"- 规模：{len(df):,} 行 × {df.shape[1]} 列")
        lines.append("- 字段（空值率 / 样例）：")
        for col in df.columns:
            null_ratio = df[col].isna().mean()
            samples = df[col].dropna().head(2).tolist()
            lines.append(f"  - `{col}`：null {null_ratio:.1%}｜例：{samples}")
        lines.append("")

    # 关键统计（供口径设计参考）
    orders = pd.read_csv(RAW / "olist_orders_dataset.csv", low_memory=False)
    payments = pd.read_csv(RAW / "olist_order_payments_dataset.csv", low_memory=False)
    items = pd.read_csv(RAW / "olist_order_items_dataset.csv", low_memory=False)
    lines.append("## 关键统计")
    lines.append(
        f"- 订单时间范围：{orders['order_purchase_timestamp'].min()} ~ {orders['order_purchase_timestamp'].max()}"
    )
    lines.append(f"- 订单状态分布：{orders['order_status'].value_counts().to_dict()}")
    lines.append(
        f"- 支付金额 payment_value 合计：{payments['payment_value'].sum():,.0f}"
        f"｜均值：{payments['payment_value'].mean():,.2f}"
    )
    lines.append(
        f"- 商品金额 price 合计：{items['price'].sum():,.0f}"
        f"｜运费 freight_value 合计：{items['freight_value'].sum():,.0f}"
    )

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"[profile] written -> {OUT}")


if __name__ == "__main__":
    main()
