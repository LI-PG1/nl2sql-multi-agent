<!-- version: 0.6 -->
【任务】把自然语言问题转成一条 SQLite 查询（仅一条 SELECT）。

[可用表与字段]
{{schema_text}}

[口径]
{{metric_context}}

[默认口径约定]（问题或口径提示未特别指定时，按此执行）
- 品类：输出英文品类名（关联 category_translation.product_category_name_english）；统计品类数量（多少种 / 覆盖多少个品类）用 COUNT(DISTINCT products.product_category_name)（不关联翻译表，避开翻译缺失与多对一）；
- 商品“名称”：products 表无名称字段，涉及商品名称时用 product_id 表示；
- 商品件数：订单的商品件数＝order_items 明细行数（COUNT(*)）；不要对 order_item_id 求和；
- 客户数：按去重客户统计（COUNT(DISTINCT customer_unique_id)）；
- 配送时长/配送天数：julianday(签收时间) − julianday(下单时间)，样本为签收时间非空的订单；
- “订单平均 X”（如订单平均商品金额）：X 合计 ÷ 订单量（COUNT(DISTINCT order_id)），不是对明细行 AVG(X)；未限定“订单”的“平均 X”才用行级 AVG(X)；
- 客单价：销售额（默认 payment_value 合计）÷ 订单量；
- 时间：筛选与分组统一按下单时间（order_purchase_timestamp，需 JOIN orders）；含评价类统计同样按下单时间，禁止使用 review_creation_date、review_answer_timestamp 等其他表的日期字段；年份条件用 LIKE '2018%'；分组标签用 strftime('%Y-%m', 时间字段)（例：“2018 年各月平均评分” → GROUP BY strftime('%Y-%m', orders.order_purchase_timestamp)）。

[示例]
{{few_shots}}

[问题]
{{question}}

[要求]
- 只输出一条 SELECT 语句，只使用上方给出的表与字段；
- 需要限制行数时使用 LIMIT；
- 输出格式：```sql ... ```
