<!-- version: 0.1 -->
【任务】从用户问题中识别：① 涉及的业务指标；② 是否存在口径歧义。

[口径表]
{{metrics_yaml}}

[用户问题]
{{question}}

[要求]
输出 JSON：{"metrics": ["..."], "ambiguity_group": "..." 或 null}
只输出 JSON，不要解释。

<!-- 注：当前阶段理解节点为规则实现（config/metrics.yaml 触发词）；
     本提示词供升级 LLM 版理解节点时使用。 -->
