"""从 data/raw/olist/*.csv 构建 SQLite 数据库 data/orders.db。"""

from __future__ import annotations

import pathlib
import sqlite3

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "olist"
DB = ROOT / "data" / "orders.db"

# CSV -> 表名（保留干净英文表名，方便 SQL 生成）
TABLES = {
    "olist_orders_dataset.csv": "orders",
    "olist_order_items_dataset.csv": "order_items",
    "olist_order_payments_dataset.csv": "order_payments",
    "olist_order_reviews_dataset.csv": "order_reviews",
    "olist_customers_dataset.csv": "customers",
    "olist_products_dataset.csv": "products",
    "olist_sellers_dataset.csv": "sellers",
    "olist_geolocation_dataset.csv": "geolocation",
    "product_category_name_translation.csv": "category_translation",
}

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_orders_order ON orders(order_id)",
    "CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_id)",
    "CREATE INDEX IF NOT EXISTS idx_items_order ON order_items(order_id)",
    "CREATE INDEX IF NOT EXISTS idx_items_product ON order_items(product_id)",
    "CREATE INDEX IF NOT EXISTS idx_payments_order ON order_payments(order_id)",
    "CREATE INDEX IF NOT EXISTS idx_reviews_order ON order_reviews(order_id)",
    "CREATE INDEX IF NOT EXISTS idx_customers_customer ON customers(customer_id)",
    "CREATE INDEX IF NOT EXISTS idx_products_product ON products(product_id)",
]


def main() -> None:
    DB.parent.mkdir(parents=True, exist_ok=True)
    if DB.exists():
        DB.unlink()
    con = sqlite3.connect(DB)
    for csv_name, table in TABLES.items():
        df = pd.read_csv(RAW / csv_name, low_memory=False)
        df.to_sql(table, con, if_exists="replace", index=False)
        print(f"[build_db] {table:20s} {len(df):>10,} rows")
    for stmt in INDEXES:
        con.execute(stmt)
    con.commit()
    print("[build_db] indexes created")
    print(f"[build_db] done -> {DB} ({DB.stat().st_size / 1e6:.1f} MB)")
    con.close()


if __name__ == "__main__":
    main()
