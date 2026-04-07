# Olist 数据画像（自动生成，脚本：scripts/profile_data.py）

## olist_customers_dataset.csv
- 规模：99,441 行 × 5 列
- 字段（空值率 / 样例）：
  - `customer_id`：null 0.0%｜例：['06b8999e2fba1a1fbc88172c00ba8bc7', '18955e83d337fd6b2def6b18a428ac77']
  - `customer_unique_id`：null 0.0%｜例：['861eff4711a542e4b93843c6dd7febb0', '290c77bc529b7ac935b93aa66c333dc3']
  - `customer_zip_code_prefix`：null 0.0%｜例：[14409, 9790]
  - `customer_city`：null 0.0%｜例：['franca', 'sao bernardo do campo']
  - `customer_state`：null 0.0%｜例：['SP', 'SP']

## olist_geolocation_dataset.csv
- 规模：1,000,163 行 × 5 列
- 字段（空值率 / 样例）：
  - `geolocation_zip_code_prefix`：null 0.0%｜例：[1037, 1046]
  - `geolocation_lat`：null 0.0%｜例：[-23.54562128115268, -23.54608112703553]
  - `geolocation_lng`：null 0.0%｜例：[-46.63929204800168, -46.64482029837157]
  - `geolocation_city`：null 0.0%｜例：['sao paulo', 'sao paulo']
  - `geolocation_state`：null 0.0%｜例：['SP', 'SP']

## olist_order_items_dataset.csv
- 规模：112,650 行 × 7 列
- 字段（空值率 / 样例）：
  - `order_id`：null 0.0%｜例：['00010242fe8c5a6d1ba2dd792cb16214', '00018f77f2f0320c557190d7a144bdd3']
  - `order_item_id`：null 0.0%｜例：[1, 1]
  - `product_id`：null 0.0%｜例：['4244733e06e7ecb4970a6e2683c13e61', 'e5f2d52b802189ee658865ca93d83a8f']
  - `seller_id`：null 0.0%｜例：['48436dade18ac8b2bce089ec2a041202', 'dd7ddc04e1b6c2c614352b383efe2d36']
  - `shipping_limit_date`：null 0.0%｜例：['2017-09-19 09:45:35', '2017-05-03 11:05:13']
  - `price`：null 0.0%｜例：[58.9, 239.9]
  - `freight_value`：null 0.0%｜例：[13.29, 19.93]

## olist_order_payments_dataset.csv
- 规模：103,886 行 × 5 列
- 字段（空值率 / 样例）：
  - `order_id`：null 0.0%｜例：['b81ef226f3fe1789b1e8b2acac839d17', 'a9810da82917af2d9aefd1278f1dcfa0']
  - `payment_sequential`：null 0.0%｜例：[1, 1]
  - `payment_type`：null 0.0%｜例：['credit_card', 'credit_card']
  - `payment_installments`：null 0.0%｜例：[8, 1]
  - `payment_value`：null 0.0%｜例：[99.33, 24.39]

## olist_order_reviews_dataset.csv
- 规模：99,224 行 × 7 列
- 字段（空值率 / 样例）：
  - `review_id`：null 0.0%｜例：['7bc2406110b926393aa56f80a40eba40', '80e641a11e56f04c1ad469d5645fdfde']
  - `order_id`：null 0.0%｜例：['73fc7af87114b39712e6da79b0a377eb', 'a548910a1c6147796b98fdf73dbeba33']
  - `review_score`：null 0.0%｜例：[4, 5]
  - `review_comment_title`：null 88.3%｜例：['recomendo', 'Super recomendo']
  - `review_comment_message`：null 58.7%｜例：['Recebi bem antes do prazo estipulado.', 'Parabéns lojas lannister adorei comprar pela Internet seguro e prático Parabéns a todos feliz Páscoa']
  - `review_creation_date`：null 0.0%｜例：['2018-01-18 00:00:00', '2018-03-10 00:00:00']
  - `review_answer_timestamp`：null 0.0%｜例：['2018-01-18 21:46:59', '2018-03-11 03:05:13']

## olist_orders_dataset.csv
- 规模：99,441 行 × 8 列
- 字段（空值率 / 样例）：
  - `order_id`：null 0.0%｜例：['e481f51cbdc54678b7cc49136f2d6af7', '53cdb2fc8bc7dce0b6741e2150273451']
  - `customer_id`：null 0.0%｜例：['9ef432eb6251297304e76186b10a928d', 'b0830fb4747a6c6d20dea0b8c802d7ef']
  - `order_status`：null 0.0%｜例：['delivered', 'delivered']
  - `order_purchase_timestamp`：null 0.0%｜例：['2017-10-02 10:56:33', '2018-07-24 20:41:37']
  - `order_approved_at`：null 0.2%｜例：['2017-10-02 11:07:15', '2018-07-26 03:24:27']
  - `order_delivered_carrier_date`：null 1.8%｜例：['2017-10-04 19:55:00', '2018-07-26 14:31:00']
  - `order_delivered_customer_date`：null 3.0%｜例：['2017-10-10 21:25:13', '2018-08-07 15:27:45']
  - `order_estimated_delivery_date`：null 0.0%｜例：['2017-10-18 00:00:00', '2018-08-13 00:00:00']

## olist_products_dataset.csv
- 规模：32,951 行 × 9 列
- 字段（空值率 / 样例）：
  - `product_id`：null 0.0%｜例：['1e9e8ef04dbcff4541ed26657ea517e5', '3aa071139cb16b67ca9e5dea641aaa2f']
  - `product_category_name`：null 1.9%｜例：['perfumaria', 'artes']
  - `product_name_lenght`：null 1.9%｜例：[40.0, 44.0]
  - `product_description_lenght`：null 1.9%｜例：[287.0, 276.0]
  - `product_photos_qty`：null 1.9%｜例：[1.0, 1.0]
  - `product_weight_g`：null 0.0%｜例：[225.0, 1000.0]
  - `product_length_cm`：null 0.0%｜例：[16.0, 30.0]
  - `product_height_cm`：null 0.0%｜例：[10.0, 18.0]
  - `product_width_cm`：null 0.0%｜例：[14.0, 20.0]

## olist_sellers_dataset.csv
- 规模：3,095 行 × 4 列
- 字段（空值率 / 样例）：
  - `seller_id`：null 0.0%｜例：['3442f8959a84dea7ee197c632cb2df15', 'd1b65fc7debc3361ea86b5f14c68d2e2']
  - `seller_zip_code_prefix`：null 0.0%｜例：[13023, 13844]
  - `seller_city`：null 0.0%｜例：['campinas', 'mogi guacu']
  - `seller_state`：null 0.0%｜例：['SP', 'SP']

## product_category_name_translation.csv
- 规模：71 行 × 2 列
- 字段（空值率 / 样例）：
  - `product_category_name`：null 0.0%｜例：['beleza_saude', 'informatica_acessorios']
  - `product_category_name_english`：null 0.0%｜例：['health_beauty', 'computers_accessories']

## 关键统计
- 订单时间范围：2016-09-04 21:15:19 ~ 2018-10-17 17:30:18
- 订单状态分布：{'delivered': 96478, 'shipped': 1107, 'canceled': 625, 'unavailable': 609, 'invoiced': 314, 'processing': 301, 'created': 5, 'approved': 2}
- 支付金额 payment_value 合计：16,008,872｜均值：154.10
- 商品金额 price 合计：13,591,644｜运费 freight_value 合计：2,251,910