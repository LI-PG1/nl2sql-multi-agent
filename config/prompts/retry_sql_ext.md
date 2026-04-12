<!-- version: 0.1 -->
【任务】上一次 SQL 执行失败，请修正后重新生成（仍只输出一条 SELECT）。

[可用表与字段]
{{schema_text}}

[背景提示]
{{evidence}}

[问题]
{{question}}

[失败信息]
错误类型：{{error_type}}
错误信息：{{error}}
上一次 SQL：
{{last_sql}}

[要求]
- 针对错误信息修正：表名 / 字段名必须来自上方"可用表与字段"；
- 只输出一条 SELECT，输出格式：```sql ... ```
