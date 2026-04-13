"""命令行入口：全链路 Pipeline（起点 → 展开 → 收束）。

用法：
    python app.py "平台的销售额是多少？"            # 真实 LLM（需 .env 配置 LLM_API_KEY），交互式澄清
    python app.py --mock "2018 年各州订单量排名？"   # 离线干跑
    python app.py --mock --eval "销售额是多少？"     # 评测模式（不交互，澄清按默认口径记录）
    python app.py --mock --trace "..."               # 打印节点轨迹与时延
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from src.graph import run
from src.llm import LLM, MockLLM


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("question", nargs="?", default="平台的总销售额是多少？")
    parser.add_argument("--mock", action="store_true", help="离线干跑（不调用真实 LLM）")
    parser.add_argument("--eval", action="store_true", help="评测模式：不交互，澄清按默认口径记录")
    parser.add_argument("--trace", action="store_true", help="打印节点轨迹与时延")
    args = parser.parse_args()

    if args.mock or not os.getenv("LLM_API_KEY"):
        llm = MockLLM()
        # 离线干跑不依赖任何外部服务：检索后端一并切到本地 mock，
        # 否则 settings.yaml 指向 remote 时会去请求未启动的嵌入服务。
        from src.config import load_settings

        load_settings()["models"]["embed"]["backend"] = "mock"
        print("[dry-run] 使用 MockLLM（未调用真实 LLM；检索后端 = mock）")
    else:
        llm = LLM()
        print(f"[llm] {llm.base_url} / {llm.model}")

    mode = "eval" if args.eval else "interactive"

    def ask(q: str) -> str:
        print("\n【需要澄清】" + q)
        try:
            return input("请输入你的选择> ").strip() or "默认口径"
        except EOFError:
            return "默认口径"

    result = run(args.question, mode=mode, llm=llm, ask=(None if mode == "eval" else ask))
    final = result["final"]

    print(f"\n路径: {final.get('path')}")
    if final.get("sql"):
        print(f"SQL: {final['sql']}")
    rows = final.get("rows") or []
    if rows:
        print(f"行数: {len(rows)}")
        for r in rows[:10]:
            print("  ", r)
    print(f"\n回答: {final.get('answer', '')}")
    print(f"运行记录: {result['trace_file']}")

    if args.trace:
        print("\n--- 节点轨迹 ---")
        for n in result["trace"]["nodes"]:
            print(f"  {n['name']:<12} {n['ms']:>9.1f} ms  {n.get('out')}")


if __name__ == "__main__":
    main()
