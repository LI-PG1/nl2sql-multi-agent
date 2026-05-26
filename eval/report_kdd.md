# KDD DataSpace 公测报告（跨库外测 · 官方评测器判分）

- 日期：2026-05-26 18:09｜任务：60 条（DataSpace 公测参考包）
- 基准：KDD Cup 2026 Data Agent Track 官方基准 DataSpace（HF 镜像 HKUSTDial/DataSpace，SHA256 校验通过；410 题输入 + 60 公测参考包）
- 链路 A（单发取数）：全库 schema 注入 + gen_sql_kdd（v0.2 解题纪律：题型三分类 / 列选择 / 字段语义映射 / SQL 陷阱 / 行数自检 + 文档定向提示）+ SQL 守卫 + 执行反馈重试（含空结果软失败）；链路 B（取数+解读）：A 的结果 → 解读阶段 → 最终答案表 CSV（与项目自身 interpret 阶段同构；解读不可解析则回退 A）
- 任务形态适配（非口径放宽）：任务本地工作区统一入库；doc/*.md 与 doc/*.pdf 全文分块入 `doc_chunks` 表（可 SQL LIKE 检索）；knowledge.md 作 evidence；守卫 LIMIT 1000→20000（gold 最大 12962 行）
- 判分：**官方评测器**（BugMaker-Boyan/DataSpace，vendored 于 eval/kdd_evaluator/；列一对一对齐 + 类型感知值比对 + 重复行多重性 + 顺序敏感项按序）——Task Accuracy 二值判定，结果格式（数值精度/尾零）由评测器按各题配置归一化
- 边界披露：视频模态本环境不可解析（28/60 题工作区含视频）；PDF 仅抽取前 10 页文本；knowledge.md 截断至 16000 字符

## 总指标（Task Accuracy，官方评测器）

| 分层 | A 单发取数 通过/总数 | B 取数+解读 通过/总数 |
|---|---|---|
| 全部公测任务 | 14/60（23.3%） | 12/60（20.0%） |
| 无需视频证据 | 8/32（25.0%） | 7/32（21.9%） |
| 需视频证据（本环境不可解析） | 6/28（21.4%） | 5/28（17.9%） |
| 顺序敏感 | 2/13（15.4%） | 2/13（15.4%） |
| 顺序不敏感 | 12/47（25.5%） | 10/47（21.3%） |

## 失败清单（48 条，链路「B 取数+解读」角度；含官方错误码与链路诊断）

| 任务 | 需视频 | gold 行 | 预测行 | 官方错误码 | 链路诊断 |
|---|---|---|---|---|---|
| task_18 | 否 | 3 | 3 | relation_mismatch | 解读后 3 行 |
| task_23 | 是 | 2 | 1 | column_count_mismatch | 解读后 0 行 |
| task_33 | 否 | 12962 | 1025 | row_count_mismatch | 解读调用失败：Error code: 400 - {'error': {'message': "This model's |
| task_35 | 否 | 3 | 0 | column_count_mismatch | 查询结果为空 |
| task_43 | 否 | 12 | 10 | row_count_mismatch | 解读后 10 行 |
| task_44 | 否 | 4 | 0 | row_count_mismatch | exec:no_such_table: no such table: patient
提示：该名称不在本库真实表/列中。请核对 [可用表与字 |
| task_48 | 是 | 3 | 1 | column_count_mismatch | 解读后 1 行 |
| task_58 | 否 | 50 | 2020 | row_count_mismatch | 结果超解读输入上限（跳过解读） |
| task_85 | 是 | 5 | 5 | relation_mismatch | 解读后 5 行 |
| task_86 | 否 | 1 | 2 | row_count_mismatch | 解读后 2 行 |
| task_102 | 是 | 1 | 13 | row_count_mismatch | 解读后 0 行 |
| task_111 | 是 | 1 | 5 | row_count_mismatch | 解读后 5 行 |
| task_123 | 是 | 1 | 1 | column_count_mismatch | 解读后 1 行 |
| task_128 | 是 | 2 | 2 | relation_mismatch | 解读后 2 行 |
| task_129 | 否 | 4 | 17 | row_count_mismatch | 解读后 17 行 |
| task_133 | 是 | 1 | 1 | relation_mismatch | 解读后 1 行 |
| task_140 | 否 | 1 | 1 | relation_mismatch | 解读后 1 行 |
| task_159 | 是 | 1 | 1 | relation_mismatch | 解读后 1 行 |
| task_166 | 否 | 1 | 1 | relation_mismatch | 解读后 1 行 |
| task_168 | 否 | 30 | 1 | row_count_mismatch | 解读后 1 行 |
| task_182 | 否 | 12 | 1 | column_count_mismatch | 解读后 1 行 |
| task_184 | 是 | 374 | 374 | relation_mismatch | 结果超解读输入上限（跳过解读） |
| task_196 | 否 | 47 | 47 | column_count_mismatch | 解读后 47 行 |
| task_200 | 否 | 9 | 0 | column_count_mismatch | 查询结果为空 |
| task_208 | 否 | 1 | 2 | row_count_mismatch | 解读后 2 行 |
| task_215 | 是 | 10 | 0 | column_count_mismatch | exec:other: ambiguous column name: ChiName |
| task_257 | 否 | 17 | 8056 | column_count_mismatch | 结果超解读输入上限（跳过解读） |
| task_259 | 否 | 12962 | 12962 | relation_mismatch | 结果超解读输入上限（跳过解读） |
| task_263 | 是 | 306 | 306 | column_count_mismatch | 解读后 306 行 |
| task_267 | 否 | 10 | 10 | relation_mismatch | 解读后 10 行 |
| task_279 | 是 | 1 | 0 | column_count_mismatch | 查询结果为空 |
| task_285 | 否 | 1 | 1 | relation_mismatch | 解读后 1 行 |
| task_298 | 是 | 1 | 1 | relation_mismatch | 解读后 1 行 |
| task_300 | 是 | 50 | 37 | row_count_mismatch | 结果超解读输入上限（跳过解读） |
| task_312 | 是 | 242 | 242 | relation_mismatch | 解读后 242 行 |
| task_322 | 否 | 331 | 6 | row_count_mismatch | 解读后 6 行 |
| task_328 | 是 | 4 | 48 | row_count_mismatch | 解读后 48 行 |
| task_329 | 否 | 1 | 2 | column_count_mismatch | 解读后 2 行 |
| task_337 | 否 | 1 | 3 | row_count_mismatch | 解读后 3 行 |
| task_361 | 是 | 1 | 1 | relation_mismatch | 解读后 1 行 |
| task_362 | 是 | 306 | 306 | column_count_mismatch | 解读后 306 行 |
| task_380 | 否 | 1 | 514 | row_count_mismatch | 解读后 514 行 |
| task_382 | 是 | 1 | 1 | column_count_mismatch | 解读后 1 行 |
| task_389 | 是 | 50 | 1 | row_count_mismatch | 解读后 1 行 |
| task_391 | 否 | 1 | 1 | column_count_mismatch | 解读后 1 行 |
| task_398 | 是 | 18 | 64 | row_count_mismatch | 解读后 64 行 |
| task_402 | 是 | 1 | 0 | row_count_mismatch | 查询结果为空 |
| task_409 | 否 | 1 | 1 | column_count_mismatch | 解读后 1 行 |

## 逐题结果

| 任务 | 需视频 | A | B | gold 行 | A 预测行 | 解读 | 尝试 | 耗时 ms |
|---|---|---|---|---|---|---|---|---|
| task_10 | 否 | √ | √ | 242 | 242 | 242行 | 2 | 2668.2 |
| task_18 | 否 | × | × | 3 | 27 | 3行 | 2 | 5813.5 |
| task_23 | 是 | × | × | 2 | 1 | 0行 | 1 | 12659.4 |
| task_24 | 是 | √ | √ | 1 | 1 | 1行 | 2 | 10175.4 |
| task_33 | 否 | × | × | 12962 | 1025 | 回退 | 1 | 1979.7 |
| task_35 | 否 | × | × | 3 | 0 | — | 3 | 28297.0 |
| task_43 | 否 | × | × | 12 | 10 | 10行 | 1 | 1702.8 |
| task_44 | 否 | × | × | 4 | 0 | — | 3 | 20948.1 |
| task_48 | 是 | × | × | 3 | 2 | 1行 | 1 | 18778.8 |
| task_58 | 否 | × | × | 50 | 2020 | 回退 | 2 | 8800.0 |
| task_85 | 是 | × | × | 5 | 5 | 5行 | 1 | 4380.0 |
| task_86 | 否 | × | × | 1 | 2 | 2行 | 1 | 1357.8 |
| task_87 | 否 | √ | √ | 1 | 1 | 1行 | 1 | 18893.7 |
| task_102 | 是 | × | × | 1 | 13 | 0行 | 1 | 7169.6 |
| task_103 | 是 | √ | √ | 23 | 23 | 回退 | 1 | 4219.3 |
| task_111 | 是 | × | × | 1 | 5 | 5行 | 3 | 11297.3 |
| task_123 | 是 | × | × | 1 | 1 | 1行 | 1 | 4327.4 |
| task_127 | 否 | √ | √ | 1 | 1 | 1行 | 1 | 1307.8 |
| task_128 | 是 | × | × | 2 | 2 | 2行 | 1 | 13399.8 |
| task_129 | 否 | × | × | 4 | 10 | 17行 | 1 | 35304.8 |
| task_133 | 是 | × | × | 1 | 15 | 1行 | 1 | 878.1 |
| task_140 | 否 | × | × | 1 | 1 | 1行 | 1 | 2238.3 |
| task_159 | 是 | × | × | 1 | 1 | 1行 | 2 | 9173.5 |
| task_166 | 否 | × | × | 1 | 5 | 1行 | 1 | 9390.2 |
| task_168 | 否 | × | × | 30 | 1 | 1行 | 1 | 38782.8 |
| task_182 | 否 | × | × | 12 | 1 | 1行 | 1 | 347816.5 |
| task_184 | 是 | × | × | 374 | 374 | 回退 | 1 | 2205.6 |
| task_196 | 否 | × | × | 47 | 47 | 47行 | 1 | 3185.0 |
| task_199 | 是 | √ | √ | 2 | 2 | 2行 | 1 | 2614.1 |
| task_200 | 否 | × | × | 9 | 0 | — | 3 | 4411.0 |
| task_208 | 否 | × | × | 1 | 1 | 2行 | 1 | 20352.8 |
| task_215 | 是 | × | × | 10 | 0 | — | 3 | 76940.5 |
| task_257 | 否 | × | × | 17 | 8056 | 回退 | 1 | 28627.5 |
| task_259 | 否 | × | × | 12962 | 12962 | 回退 | 1 | 16432.1 |
| task_263 | 是 | × | × | 306 | 306 | 306行 | 1 | 1617.9 |
| task_264 | 否 | √ | √ | 35 | 35 | 35行 | 1 | 2677.6 |
| task_267 | 否 | √ | × | 10 | 10 | 10行 | 1 | 1655.5 |
| task_269 | 否 | √ | √ | 2 | 2 | 2行 | 1 | 4093.6 |
| task_279 | 是 | × | × | 1 | 0 | — | 3 | 23220.4 |
| task_285 | 否 | × | × | 1 | 1 | 1行 | 1 | 1626.8 |
| task_298 | 是 | × | × | 1 | 1 | 1行 | 1 | 1545.3 |
| task_300 | 是 | × | × | 50 | 37 | 回退 | 2 | 33111.8 |
| task_312 | 是 | √ | × | 242 | 242 | 242行 | 2 | 20485.0 |
| task_322 | 否 | × | × | 331 | 1 | 6行 | 1 | 18617.1 |
| task_326 | 否 | √ | √ | 1 | 1 | 1行 | 1 | 4788.4 |
| task_328 | 是 | × | × | 4 | 48 | 48行 | 3 | 32281.2 |
| task_329 | 否 | × | × | 1 | 2 | 2行 | 2 | 22683.3 |
| task_336 | 是 | √ | √ | 1 | 1 | 1行 | 1 | 1418.1 |
| task_337 | 否 | × | × | 1 | 3 | 3行 | 1 | 11687.2 |
| task_361 | 是 | × | × | 1 | 1 | 1行 | 1 | 10051.6 |
| task_362 | 是 | × | × | 306 | 306 | 306行 | 1 | 5853.4 |
| task_371 | 否 | √ | √ | 12962 | 12962 | 回退 | 1 | 1256.1 |
| task_378 | 是 | √ | √ | 1 | 1 | 1行 | 1 | 9968.4 |
| task_380 | 否 | × | × | 1 | 514 | 514行 | 1 | 1067.4 |
| task_382 | 是 | × | × | 1 | 1 | 1行 | 1 | 15872.6 |
| task_389 | 是 | × | × | 50 | 1 | 1行 | 1 | 26680.2 |
| task_391 | 否 | × | × | 1 | 1 | 1行 | 1 | 1329.8 |
| task_398 | 是 | × | × | 18 | 64 | 64行 | 2 | 22524.3 |
| task_402 | 是 | × | × | 1 | 0 | — | 3 | 12443.5 |
| task_409 | 否 | × | × | 1 | 17 | 1行 | 1 | 49831.3 |

## 复现方式

```bash
python eval/kdd_prepare.py      # 60 个工作区 → 任务库 + doc_chunks + 简报（已随仓库数据就绪可跳过）
python eval/run_kdd.py --limit 5   # 冒烟
python eval/run_kdd.py             # 全量 60 题（链路 A/B）
python eval/run_kdd.py --judge     # 官方评测器判分 + 本报告
```

数据与评测器来源：HF 镜像 HKUSTDial/DataSpace（MIT）；官方评测器 github.com/BugMaker-Boyan/DataSpace（MIT）。