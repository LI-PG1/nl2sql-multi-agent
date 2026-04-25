# 检索后端对照报告（BGE vs Qwen3-Embedding-4B · 索引层）

- 日期：2026-05-12 23:59｜方法：同语料重算向量 + 同探针 Top-3 命中对照（隔离上层加权）
- 语料：schema_index 61 条（表/字段块）+ doc_index 18 条（口径/字典块）
- 探针：Linking 11 条（期望表全进 Top-3）+ 口径文档 12 条（Top-3 块含关键词）

## 总指标

| 指标 | BGE（现役） | Qwen3-Embedding-4B（自部署） |
|---|---|---|
| 向量维度 | 512 | 2560 |
| Linking 探针命中 | 7/11 = 64% | **9/11 = 82%** |
| 文档探针命中 | 12/12 = 100% | **12/12 = 100%** |
| 合计 | 19/23 | **21/23** |
| 语料编码耗时 | 0.5 s（本地） | 2.5 s（公网服务） |
| 单查询平均时延 | 6.0 ms（本地） | 344.8 ms（公网） |

## 明细 · bge

### Linking 探针

| 通过 | 问题 | 期望表 | Top-3 |
|---|---|---|---|
| √ | 各订单状态的订单量分布？ | orders | orders, order_items, order_reviews |
| √ | 评分低于 3 分的订单有多少？ | order_reviews | order_reviews, order_items, customers |
| √ | 各支付方式的支付金额合计是多少？ | order_payments | order_payments, order_items, orders |
| × | 2018 年各州的销售额排名前五？ | orders,order_payments,customers | customers, sellers, order_items |
| √ | 各品类商品的商品金额排名前十？ | order_items,products | order_items, products, category_translation |
| × | 卖家所在州的订单量分布？ | sellers,order_items,orders | order_items, customers, sellers |
| × | 平均配送天数最长的五个州？ | orders,customers | customers, sellers, geolocation |
| √ | 销售额是多少？ | order_payments | order_items, order_payments, customers |
| √ | 不含运费的销售额是多少？ | order_items | order_items, customers, orders |
| √ | 净销售额是多少？ | order_payments | order_items, order_payments, customers |
| × | 2018 年逐月的销售趋势？ | orders,order_payments | orders, order_items, customers |

### 文档探针

| 通过 | 问题 | Top-1 命中块 |
|---|---|---|
| √ | 客单价的口径怎么定义的？ | 指标：客单价（avg_order_value） |
| √ | 订单量是如何统计的？ | 指标：订单平均商品金额（order_avg_item_amount） |
| √ | 配送时长怎么算？ | 指标：配送时长（配送天数）（delivery_days） |
| √ | 客户数去重按哪个字段？ | 表：customers |
| √ | 销售额默认包含哪些金额？ | 口径组：金额范围（amount_scope） |
| √ | 取消的订单在统计里怎么处理？ | 口径组：取消订单处理（cancel_handling） |
| √ | 时间口径默认用哪个字段？ | 口径组：时间口径（time_field） |
| √ | 订单平均商品金额怎么定义？ | 指标：订单平均商品金额（order_avg_item_amount） |
| √ | 支付金额存在哪张表？ | 表：order_payments |
| √ | 评分数据在哪个表？ | 表：order_reviews |
| √ | 品类的中英文对照在哪里？ | 表：category_translation |
| √ | 买家所在城市和州在哪个表？ | 表：sellers |

## 明细 · remote

### Linking 探针

| 通过 | 问题 | 期望表 | Top-3 |
|---|---|---|---|
| √ | 各订单状态的订单量分布？ | orders | orders, order_payments, customers |
| √ | 评分低于 3 分的订单有多少？ | order_reviews | order_reviews, orders, order_payments |
| √ | 各支付方式的支付金额合计是多少？ | order_payments | order_payments, order_items, orders |
| × | 2018 年各州的销售额排名前五？ | orders,order_payments,customers | sellers, order_payments, geolocation |
| √ | 各品类商品的商品金额排名前十？ | order_items,products | products, order_items, order_payments |
| √ | 卖家所在州的订单量分布？ | sellers,order_items,orders | sellers, order_items, orders |
| × | 平均配送天数最长的五个州？ | orders,customers | sellers, orders, geolocation |
| √ | 销售额是多少？ | order_payments | order_payments, order_items, sellers |
| √ | 不含运费的销售额是多少？ | order_items | order_items, order_payments, sellers |
| √ | 净销售额是多少？ | order_payments | order_payments, sellers, order_items |
| √ | 2018 年逐月的销售趋势？ | orders,order_payments | sellers, order_payments, orders |

### 文档探针

| 通过 | 问题 | Top-1 命中块 |
|---|---|---|
| √ | 客单价的口径怎么定义的？ | 指标：客单价（avg_order_value） |
| √ | 订单量是如何统计的？ | 指标：订单量（order_count） |
| √ | 配送时长怎么算？ | 指标：配送时长（配送天数）（delivery_days） |
| √ | 客户数去重按哪个字段？ | 指标：客户数（customer_count） |
| √ | 销售额默认包含哪些金额？ | 口径组：金额范围（amount_scope） |
| √ | 取消的订单在统计里怎么处理？ | 口径组：取消订单处理（cancel_handling） |
| √ | 时间口径默认用哪个字段？ | 口径组：时间口径（time_field） |
| √ | 订单平均商品金额怎么定义？ | 指标：订单平均商品金额（order_avg_item_amount） |
| √ | 支付金额存在哪张表？ | 表：order_payments |
| √ | 评分数据在哪个表？ | 表：order_reviews |
| √ | 品类的中英文对照在哪里？ | 表：category_translation |
| √ | 买家所在城市和州在哪个表？ | 表：sellers |

## 裁决

- 合计命中：BGE 19 vs 4B 21（共 23 条探针）
- **Qwen3-Embedding-4B ≥ BGE → 建议接入**（切换 settings.embed.backend=remote，重建三层索引）
