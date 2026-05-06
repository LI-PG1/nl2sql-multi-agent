# 规模与引擎实验室报告（data-agent）

- 日期：2026-05-12｜方法＝**真实分布扩样压测 ＋ 双引擎一致性**（可复现：`python scripts/scale_lab.py`）
- 环境：SQLite 3.49.1｜DuckDB 1.5.6｜本机 18 核

## 一、真实数据盘点（当前项目实际持有）

| 资产 | 规模 |
|---|---|
| Olist 主库（9 表） | **1,550,922 行 / 143 MB**（orders 99,441｜order_items 112,650｜geolocation 1,000,163） |
| BIRD Mini-Dev（外测） | 11 库 / 约 390 万行（最大表 financial.trans 1,056,320 行）/ 500 题 |
| 在库公开数据集 | 8 套 / 约 3.3 GB（含 Spider 876 MB、Archer 315 MB、BIRD 1.4 GB） |

> 定位：该规模与 NL2SQL 公开基准（Spider / BIRD 本体）同量级；生产形态下库在数仓、执行下推，
> 系统层不搬运数据——**"数据量"的正确验证方式＝规模行为（本次 E1/E2）与引擎可迁移性（E3）**，
> 而非把演示库人为做大。放大实验仅用于压测，非业务数据。

## 二、E1 规模放大（100×，压测）

- 事实表 `fact_items`：**11,265,000 行**（= order_items 112,650 × 100；列值全部来自真实数据分布）
- 构建耗时：SQLite 27.3s → DuckDB 镜像 28.6s（sqlite 扩展直读）
- 落盘体积：SQLite **1634.2 MB**｜DuckDB（列存）**212.0 MB**

## 三、E2 引擎对比（同一事实表、同一查询；预热后 3 轮取最优）

| 查询 | SQLite 无索引 | SQLite + 索引 | DuckDB（列存） |
|---|---|---|---|
| Q1 全量聚合（月营收 GROUP BY） | 6615.6 ms | — | 7.1 ms |
| Q2 维度聚合（品类 Top10） | 5474.5 ms | — | 9.8 ms |
| Q3 选择性过滤（单商品求和） | 1351.4 ms | **0.0 ms** | 2.5 ms（索引 0.8 ms） |
| Q4 点查（单订单计数） | 1335.9 ms | **0.0 ms** | 0.9 ms（索引 0.4 ms） |

- 索引构建耗时：SQLite 17.0s｜DuckDB 5.6s
- 读法：全量聚合看列存优势；点查/过滤看行存必须依赖索引——这正是"引擎选择与索引策略要随数据形态走"的证据。

## 四、E3 引擎一致性（黄金集执行型 SQL × 双引擎）

- 共 52 条：DuckDB 直执成功 **46**，sqlglot 转译后成功 **4**，失败 2
- 与 SQLite 基准结果**一致 49/52 = 94.2%**
- 差异示例（前 3 条）：[{"id": "g-020", "mode": "failed", "err": "CatalogException: Catalog Error: Scalar Function with name julianday does not exist!\nDid", "duck_n": null, "lite_n": 1}, {"id": "g-037", "mode": "direct", "err": "", "duck_n": 27, "lite_n": 27}, {"id": "g-052", "mode": "failed", "err": "BinderException: Binder Error: Could not choose a best candidate function for the funct", "duck_n": null, "lite_n": 8}]

## 五、结论与边界

1. **规模行为有据**：1,126.5 万行事实表上，列存引擎全量聚合 7.1 ms、行存依赖索引把点查从 1335.9 ms 降到 0.0 ms——"换引擎/建索引"的系统性收益被量化。
2. **引擎可迁移**：生成 SQL 双引擎一致性 49/52，差异集中在方言函数——已实证"转译层（sqlglot）"是成本可控的迁移路径（对接数仓的前置能力）。
3. **范围**：① 放大为压测（合成数据，非业务真实数据）；② 单连接形态，并发压测为后续扩展项；③ 生产化差距与迁移路径见 `docs/生产化路线.md`。
