# 外测报告（BIRD Mini-Dev · 公开基准自测）

- 日期：2026-05-12 21:06｜用例：10 条｜总耗时：0.1 min
- 定位：**跨库泛化外测**（未经专门适配的库）——公开基准上的本地自测数字
- 外测模式：全库 schema 注入（含类型/PK/外键）+ 生成模板 + 守卫 + 执行反馈重试（≤2）+ 规范化结果集比对；Olist 专属口径层 / 索引压缩 / few-shot 在外测中关闭（公平性设计，见脚本 docstring）
- 模型：见运行环境（自部署端点，settings/.env）｜结果集比对上限 1000 行（两侧同口径）

## 总指标

| 指标 | 数值 |
|---|---|
| **执行准确率（EX）** | **5/10 = 50.0%** |
| 跳过（参考集为空等） | 0 |

## 分层（难度）

| 难度 | 条数 | 通过 | 通过率 |
|---|---|---|---|
| simple | 6 | 4 | 66.7% |
| moderate | 4 | 1 | 25.0% |

## 分层（数据库）

| 库 | 条数 | 通过 | 通过率 |
|---|---|---|---|
| california_schools | 1 | 1 | 100.0% |
| card_games | 1 | 0 | 0.0% |
| codebase_community | 1 | 0 | 0.0% |
| debit_card_specializing | 1 | 1 | 100.0% |
| european_football_2 | 1 | 1 | 100.0% |
| financial | 1 | 0 | 0.0% |
| formula_1 | 1 | 0 | 0.0% |
| student_club | 1 | 1 | 100.0% |
| superhero | 1 | 1 | 100.0% |
| thrombosis_prediction | 1 | 0 | 0.0% |

## 过程指标

- 端到端时延：P50 = 700 ms｜P95 = 1386 ms
- 重试增益：触发重试 0 条，其中重试后成功 0 条

## 失败清单（前 20）

| qid | 难度 | 库 | 问题 | 原因 |
|---|---|---|---|---|
| 340 | simple | card_games | Which are the cards that have incred | 结果集不一致 |
| 531 | simple | codebase_community | Which user has a higher reputation,  | 结果集不一致 |
| 89 | moderate | financial | How many accounts who choose issuanc | 结果集不一致 |
| 846 | moderate | formula_1 | Please list the reference names of t | 结果集不一致 |
| 1149 | moderate | thrombosis_prediction | Are there more in-patient or outpati | 结果集不一致 |