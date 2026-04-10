"""LLM 客户端：OpenAI 兼容接口（DeepSeek / 通义千问 / GLM 均可），含离线 MockLLM。"""

from __future__ import annotations

import os
import re


class LLM:
    """OpenAI 兼容 Chat 客户端。环境变量：LLM_API_KEY / LLM_BASE_URL / LLM_MODEL

    默认流式（stream=True）：自部署端点经网关转发时，非流式长请求在约 30s 后会被网关切断
    （实测 Connection error），流式持续回包可稳定完成长输出（如整表转写 / 长 SQL 重试）。
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 180.0,
    ):
        from openai import OpenAI

        self.api_key = api_key or os.getenv("LLM_API_KEY", "")
        self.base_url = base_url or os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
        self.model = model or os.getenv("LLM_MODEL", "deepseek-chat")
        if not self.api_key:
            raise RuntimeError("未配置 LLM_API_KEY（可在 .env 中设置，或用 MockLLM 干跑）")
        # timeout：单请求上限 180s（防止单条失控请求挂起整轮评测）
        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=timeout)

    def chat(
        self,
        system: str,
        user: str,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        stream: bool = True,
    ) -> str:
        kwargs = dict(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
        )
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        if not stream:
            resp = self.client.chat.completions.create(**kwargs)
            return resp.choices[0].message.content or ""
        try:
            resp = self.client.chat.completions.create(stream=True, **kwargs)
            parts: list[str] = []
            for ev in resp:
                if ev.choices and ev.choices[0].delta and ev.choices[0].delta.content:
                    parts.append(ev.choices[0].delta.content)
            return "".join(parts)
        except Exception:  # noqa: BLE001  流式不被支持时回退非流式
            resp = self.client.chat.completions.create(**kwargs)
            return resp.choices[0].message.content or ""


class MockLLM:
    """离线干跑：按节点提示词特征返回演示输出（仅用于链路自测，不代表真实效果）。

    simulate_first_error=True 时，第一次生成 SQL 故意引用错误表名，
    用于演示"执行反馈重试（环节级重跑）"。
    """

    def __init__(self, simulate_first_error: bool = False):
        self.simulate_first_error = simulate_first_error
        self._sql_calls = 0

    def chat(self, system: str, user: str, temperature: float = 0.0) -> str:
        # 结果解读节点（interpret.md 的标记）
        if "解读查询结果" in user:
            return "（干跑解读）已按所选口径完成统计，结果见上方数据。"
        # 澄清追问（模型生成兜底路径；主路径为 metrics.yaml 模板）
        if "澄清追问" in user:
            return "请补充你需要的统计口径。"
        # SQL 生成 / 重试：只依据"问题"段判断（避免被 few-shot 示例文本干扰）
        m = re.search(r"\[问题\]\s*\n(.+?)(?:\n\[|\Z)", user, re.S)
        q = m.group(1).strip() if m else user
        self._sql_calls += 1
        if self.simulate_first_error and self._sql_calls == 1:
            return "```sql\nSELECT SUM(payment_value) AS gmv FROM order_payment\n```"
        if "订单数" in q or "订单量" in q or "多少订单" in q:
            return "```sql\nSELECT COUNT(DISTINCT order_id) AS order_cnt FROM orders\n```"
        if "状态" in q or "取消" in q:
            return "```sql\nSELECT order_status, COUNT(*) AS cnt FROM orders GROUP BY order_status ORDER BY cnt DESC\n```"
        if "评分" in q or "评论" in q:
            return "```sql\nSELECT COUNT(*) AS cnt FROM order_reviews WHERE review_score < 3\n```"
        return "```sql\nSELECT ROUND(SUM(payment_value), 2) AS gmv FROM order_payments\n```"
