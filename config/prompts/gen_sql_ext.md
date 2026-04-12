<!-- version: 0.1 -->
【任务】把自然语言问题转成一条 SQLite 查询（仅一条 SELECT）。

[可用表与字段]
{{schema_text}}

[背景提示]
{{evidence}}

[问题]
{{question}}

[要求]
- 只输出一条 SELECT 语句，只使用上方给出的表与字段；
- 需要限制行数时使用 LIMIT；
- 输出格式：```sql ... ```
