# 外测报告（BIRD Mini-Dev · 公开基准自测）

- 日期：2026-05-12 21:34｜用例：500 条｜总耗时：12.2 min
- 定位：**跨库泛化外测**（未经专门适配的库）——公开基准上的本地自测数字
- 外测模式：全库 schema 注入（含类型/PK/外键）+ 生成模板 + 守卫 + 执行反馈重试（≤2）+ 规范化结果集比对；Olist 专属口径层 / 索引压缩 / few-shot 在外测中关闭（公平性设计，见脚本 docstring）
- 模型：见运行环境（自部署端点，settings/.env）｜结果集比对上限 1000 行（两侧同口径）

## 总指标

| 指标 | 数值 |
|---|---|
| **执行准确率（EX）** | **272/498 = 54.6%** |
| 跳过（参考集为空等） | 2 |

## 分层（难度）

| 难度 | 条数 | 通过 | 通过率 |
|---|---|---|---|
| simple | 148 | 97 | 65.5% |
| moderate | 249 | 131 | 52.6% |
| challenging | 101 | 44 | 43.6% |

## 分层（数据库）

| 库 | 条数 | 通过 | 通过率 |
|---|---|---|---|
| california_schools | 30 | 8 | 26.7% |
| card_games | 51 | 26 | 51.0% |
| codebase_community | 48 | 23 | 47.9% |
| debit_card_specializing | 30 | 17 | 56.7% |
| european_football_2 | 51 | 36 | 70.6% |
| financial | 32 | 14 | 43.8% |
| formula_1 | 66 | 30 | 45.5% |
| student_club | 48 | 33 | 68.8% |
| superhero | 52 | 40 | 76.9% |
| thrombosis_prediction | 50 | 26 | 52.0% |
| toxicology | 40 | 19 | 47.5% |

## 过程指标

- 端到端时延：P50 = 780 ms｜P95 = 1840 ms
- 重试增益：触发重试 28 条，其中重试后成功 7 条

## 失败清单（前 20）

| qid | 难度 | 库 | 问题 | 原因 |
|---|---|---|---|---|
| 1472 | moderate | debit_card_specializing | In 2012, who had the least consumpti | 结果集不一致 |
| 1481 | challenging | debit_card_specializing | What is the difference in the annual | 结果集不一致 |
| 1482 | challenging | debit_card_specializing | Which of the three segments—SME, LAM | 结果集不一致 |
| 1484 | simple | debit_card_specializing | How many more "discount" gas station | 结果集不一致 |
| 1498 | simple | debit_card_specializing | What is the highest monthly consumpt | 结果集不一致 |
| 1500 | simple | debit_card_specializing | Please list the product description  | 结果集不一致 |
| 1501 | moderate | debit_card_specializing | Please list the countries of the gas | 结果集不一致 |
| 1524 | simple | debit_card_specializing | What's the nationality of the custom | 结果集不一致 |
| 1525 | simple | debit_card_specializing | What is the percentage of the custom | 结果集不一致 |
| 1526 | challenging | debit_card_specializing | For the customer who paid 634.8 in 2 | 结果集不一致 |
| 1528 | simple | debit_card_specializing | What is the percentage of "premium"  | 结果集不一致 |
| 1529 | moderate | debit_card_specializing | What is the amount spent by customer | 结果集不一致 |
| 1531 | moderate | debit_card_specializing | Who is the top spending customer and | 结果集不一致 |
| 1322 | moderate | student_club | Among the events attended by more th | 结果集不一致 |
| 1338 | moderate | student_club | Was each expense in October Meeting  | 结果集不一致 |
| 1350 | moderate | student_club | What is the status of the event whic | 结果集不一致 |
| 1357 | simple | student_club | State the date Connor Hilton paid hi | 结果集不一致 |
| 1362 | simple | student_club | How many cities are there in Orange  | 结果集不一致 |
| 1387 | moderate | student_club | Which student has been entrusted to  | 执行失败：exec:no_such_column: no such column: b.link_to_member |
| 1389 | simple | student_club | Which event has the lowest cost? | 结果集不一致 |