# -*- coding: utf-8 -*-
"""数据分析管线 Web 演示（Streamlit）。

运行：  .venv/Scripts/python.exe -m streamlit run webui.py --server.port 8602
离线演示默认使用 MockLLM + 本地 mock 检索，不依赖任何外部服务；
配置了 LLM_API_KEY 时可切换真实端点。

界面布局与 ops-agent 的车机 HUD 同构：左栏对话与场景快选，右栏管线过程与结果。
"""

import os
import re
import time

import streamlit as st

st.set_page_config(
    page_title="Data Agent · 管线演示", page_icon=":material/monitoring:", layout="wide"
)

from src.config import load_settings  # noqa: E402
from src import metrics  # noqa: E402
from src.graph import run  # noqa: E402
from src.llm import LLM, MockLLM  # noqa: E402

# 离线演示默认走本地 mock 检索（与 `python -m src.graph` 干跑口径一致）
load_settings()["models"]["embed"]["backend"] = "mock"

CSS = """
<style>
:root{
  --bg:#0b1120; --panel:#111a2e; --panel2:#0e1626; --line:#1e2a44;
  --tx:#e8edf7; --tx2:#94a3b8; --tx3:#64748b;
  --acc:#38bdf8; --acc2:#818cf8; --ok:#34d399; --warn:#fbbf24; --bad:#f87171;
  --badbg:rgba(185,28,28,.16); --badln:#b91c1c;
}
header[data-testid="stHeader"]{display:none;}
.stApp{background:radial-gradient(1100px 520px at 16% -8%, #12203a 0%, var(--bg) 58%) fixed, var(--bg);
       color:var(--tx); font-size:13px;}
.block-container{padding:.9rem 1.4rem 2.2rem; max-width:1560px;}
a{color:var(--acc);}
.topbar{display:flex; align-items:center; gap:12px; padding:10px 16px; margin-bottom:14px;
        background:linear-gradient(180deg,#12203a,#0e1626); border:1px solid var(--line);
        border-radius:14px;}
.brand{font-size:17px; font-weight:700; letter-spacing:.5px;}
.brand .dot{color:var(--acc);}
.badge{font-size:11px; color:var(--tx2); border:1px solid var(--line); border-radius:999px;
       padding:3px 10px; background:rgba(148,163,184,.06); white-space:nowrap;}
.badge b{color:var(--tx); font-weight:600;}
.group-h{font-size:12px; color:var(--tx3); letter-spacing:2px; margin:14px 0 6px 2px;}
.group-h.alert{color:#fda4af;}
div[data-testid="stButton"] button{width:100%; border:1px solid var(--line)!important;
  border-radius:10px!important; background:rgba(148,163,184,.05)!important; color:var(--tx)!important;
  font-size:12.5px!important; padding:8px 10px!important;}
div[data-testid="stButton"] button:hover{border-color:var(--acc)!important;}
div[data-testid="stButton"] button.danger{border-color:var(--badln)!important; background:var(--badbg)!important; color:#fecaca!important;}
div[data-testid="stButton"] button.retry{border-color:#92400e!important; background:rgba(146,64,14,.14)!important; color:#fcd34d!important;}
.panel{background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:14px 16px; margin-bottom:12px;}
.panel h4{margin:0 0 10px 0; font-size:13px; color:var(--tx2); letter-spacing:1px; font-weight:600;}
.flow{display:flex; flex-wrap:wrap; gap:8px; align-items:stretch;}
.node{flex:1 1 108px; min-width:104px; background:var(--panel2); border:1px solid var(--line);
      border-radius:10px; padding:8px 10px; position:relative;}
.node .nm{font-size:12px; font-weight:600;}
.node .ms{font-size:10.5px; color:var(--tx3); margin-top:2px;}
.node .st-dot{width:7px; height:7px; border-radius:50%; display:inline-block; margin-right:5px;}
.node.loop{border-color:#4c1d95;}
.node .lp{font-size:9.5px; color:#c4b5fd; position:absolute; top:6px; right:8px;}
.banner{border-radius:12px; padding:12px 16px; margin-bottom:12px; font-size:13px;}
.banner.red{background:var(--badbg); border:1px solid var(--badln); color:#fecaca;}
.banner.amber{background:rgba(146,64,14,.14); border:1px solid #92400e; color:#fcd34d;}
.banner.blue{background:rgba(56,189,248,.10); border:1px solid #0ea5e9; color:#bae6fd;}
.banner .t{font-weight:700; margin-bottom:4px;}
.kv{font-size:12px; color:var(--tx2); margin:2px 0;}
.kv b{color:var(--tx);}
.sqlbox{background:#0a101f; border:1px solid var(--line); border-radius:10px; padding:10px 12px;
        font-family:ui-monospace,Consolas,monospace; font-size:12px; color:#a5f3fc; white-space:pre-wrap;}
.empty{border:1px dashed var(--line); border-radius:12px; padding:22px; text-align:center;
       color:var(--tx3); font-size:12.5px;}
.hist{font-size:11.5px; color:var(--tx2);}
.hist td, .hist th{padding:3px 10px; border-bottom:1px solid var(--line); text-align:left;}
.pill{display:inline-block; padding:1px 9px; border-radius:999px; font-size:10.5px; margin-left:6px;}
.pill.g{background:rgba(52,211,153,.14); color:var(--ok);}
.pill.y{background:rgba(251,191,36,.14); color:var(--warn);}
.pill.r{background:var(--badbg); color:var(--bad);}
div[data-testid="stExpander"]{border:1px solid var(--line)!important; border-radius:12px!important;}
div[data-testid="stExpander"] summary{color:var(--tx2)!important; font-size:12px!important;}
div[data-testid="stTextInput"] input{background:var(--panel2)!important; color:var(--tx)!important;
  border:1px solid var(--line)!important; border-radius:10px!important;}
div[data-testid="stTextArea"] textarea{background:var(--panel2)!important; color:var(--tx)!important;
  border:1px solid var(--line)!important; border-radius:10px!important; font-size:12.5px!important;}
label, .stMarkdown p{color:var(--tx2)!important; font-size:12px!important;}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

# ---------------- 后端状态 ----------------
HAS_KEY = bool(os.getenv("LLM_API_KEY"))
EMB_BACKEND = load_settings().get("models", {}).get("embed", {}).get("backend", "mock")
DB_SIZE_MB = 143

st.markdown(
    f"""
<div class="topbar">
  <div class="brand">Data Agent<span class="dot"> ·</span> 数据分析管线演示</div>
  <div class="badge">生成模型 <b>{"真实端点" if HAS_KEY else "MockLLM"}</b></div>
  <div class="badge">检索后端 <b>{"远程向量" if EMB_BACKEND == "remote" else "本地 mock"}</b></div>
  <div class="badge">业务库 <b>Olist · {DB_SIZE_MB} MB · 约 155 万行</b></div>
  <div class="badge">口径资产 <b>metrics / synonyms / schema_notes</b></div>
</div>
""",
    unsafe_allow_html=True,
)

# ---------------- 场景定义 ----------------
# (图标, 标签, key, 附加参数)
SCENE_GROUPS = [
    (
        "常规分析",
        [
            ("analytics", "各州订单量排名", "sc_normal", {}),
        ],
    ),
    (
        "口径澄清（歧义 → 追问 → 消解）",
        [
            ("help", "平台销售额（金额口径歧义）", "sc_clarify", {"canned": "支付总额"}),
        ],
    ),
    (
        "可靠性（执行报错 → 环节级重跑）",
        [
            ("replay", "订单量排名（首轮注入错表名）", "sc_retry", {"first_error": True}),
        ],
    ),
    (
        "安全拒答（变更类请求）",
        [
            ("gpp_bad", "把取消的订单从库里删掉", "sc_refuse", {"danger": True}),
        ],
    ),
]

NODE_LABELS = {
    "understand": "理解",
    "clarify": "澄清",
    "link_schema": "检索",
    "gen_sql": "生成",
    "exec_sql": "执行",
    "interpret": "解读",
    "respond": "答复",
}
PATH_PILL = {"normal": ("g", "正常"), "degrade": ("y", "降级"), "refuse": ("r", "拒答")}

# ---- 结果速读：数字可读化（展示层，不改管线输出）----
_COL_LABELS = {
    "order_cnt": "订单数",
    "gmv": "GMV（成交额）",
    "revenue": "营收",
    "amount": "金额",
    "cnt": "数量",
}
_MONEY_HINTS = ("gmv", "amount", "value", "price", "revenue", "payment", "金额", "销售额")
_COUNT_HINTS = ("cnt", "count", "num", "量", "单")


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _fmt(v):
    """千分位；整数不带小数，浮点保留两位。"""
    if _is_num(v):
        return f"{int(v):,}" if float(v).is_integer() else f"{v:,.2f}"
    return str(v)


def _col_kind(col):
    c = (col or "").lower()
    if any(h in c for h in _MONEY_HINTS):
        return "money"
    if any(h in c for h in _COUNT_HINTS):
        return "count"
    return "num"


def _wan(v):
    return f"{v / 10000:,.2f} 万" if abs(v) >= 1e4 else None


def quick_read(cols, rows):
    """把 1×1 聚合结果转成一句话；多行结果给行数概览。"""
    if not rows:
        return ""
    if len(rows) == 1 and len(cols) == 1:
        v = rows[0][0]
        kind = _col_kind(cols[0])
        label = _COL_LABELS.get(cols[0], cols[0])
        if not _is_num(v):
            return f"{label}：{_fmt(v)}"
        s = _fmt(v)
        w = _wan(v)
        if kind == "count":
            tail = f"，约 {w} 单" if w else ""
            return f"{label}：{s}{tail}"
        if kind == "money":
            tail = f"，约 {w}" if w else ""
            cur = (
                "（数据集原始货币）"
                if cols[0].lower() in ("gmv", "payment_value", "revenue")
                else ""
            )
            return f"{label}：{s}{tail}{cur}"
        tail = f"，约 {w}" if w else ""
        return f"{label}：{s}{tail}"
    return f"共 {len(rows)} 行 × {len(cols)} 列" + ("，下表展示前 12 行" if len(rows) > 12 else "")


def fmt_answer(text):
    """回答文本里的长数字加千分位（不动 4 位以内的年份等）。"""
    return re.sub(
        r"(?<![\w.])\d{5,}(?:\.\d+)?(?![\w.])", lambda m: _fmt(float(m.group())), text or ""
    )


def make_llm(first_error: bool = False, real: bool = False):
    if real and HAS_KEY:
        return LLM()
    return MockLLM(simulate_first_error=first_error)


def run_pipeline(question: str, ask=None, first_error: bool = False, real: bool = False):
    t0 = time.perf_counter()
    res = run(question, mode="interactive", llm=make_llm(first_error, real), ask=ask)
    res["wall_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return res


# ---------------- 会话状态 ----------------
ss = st.session_state
ss.setdefault("result", None)  # 最近一次 run 的返回
ss.setdefault("history", [])  # [{q, path, retries, ms}]
ss.setdefault("clarify_pending", None)  # {question, group_id}
ss.setdefault("pending_scene", None)  # 场景快选待执行

left, right = st.columns([5, 6], gap="large")

# ================= 左栏：输入与场景 =================
with left:
    st.markdown('<div class="group-h">分析请求</div>', unsafe_allow_html=True)
    q = st.text_input(
        "输入分析问题（回车提交）",
        placeholder="例：2018 年销量前 5 的品类",
        label_visibility="collapsed",
        key="free_q",
    )
    c_run, c_real = st.columns([3, 1])
    do_run = c_run.button("运行管线", type="primary", use_container_width=True)
    use_real = c_real.checkbox(
        "真实端点", value=False, help="未配置 LLM_API_KEY 时自动回落 MockLLM", disabled=not HAS_KEY
    )

    if do_run and q.strip():
        question = q.strip()
        refuse_hit = metrics.detect_mutation(question)
        amb = metrics.detect_ambiguity(question)
        if refuse_hit:
            ss.result = run_pipeline(question, real=use_real)
        elif amb:
            ss.clarify_pending = {"question": question, "group_id": amb["id"], "real": use_real}
            ss.result = None
        else:
            ss.result = run_pipeline(question, real=use_real)
        st.rerun()

    for group_name, scenes in SCENE_GROUPS:
        alert = "alert" if "安全拒答" in group_name else ""
        st.markdown(f'<div class="group-h {alert}">{group_name}</div>', unsafe_allow_html=True)
        for icon, label, key, extra in scenes:
            cls = "danger" if extra.get("danger") else ("retry" if extra.get("first_error") else "")
            if st.button(label, icon=f":material/{icon}:", key=key, use_container_width=True):
                ss.pending_scene = {"key": key, "label": label, **extra}
                st.rerun()

    # 澄清卡（自由输入命中歧义时出现）
    if ss.clarify_pending:
        g = metrics.get_group(ss.clarify_pending["group_id"])
        cq = metrics.clarify_question(g)
        st.markdown(
            f"""
        <div class="banner blue"><div class="t">检测到口径歧义，需要澄清</div>
        {cq}</div>""",
            unsafe_allow_html=True,
        )
        ans = st.text_input("补充口径", placeholder="例：只要商品金额，不含运费", key="clarify_in")
        ca, cb = st.columns(2)
        if ca.button("按补充口径重跑", type="primary", use_container_width=True):
            answer = ans.strip() or "默认口径"
            ss.result = run_pipeline(
                ss.clarify_pending["question"],
                ask=lambda _q: answer,
                real=ss.clarify_pending["real"],
            )
            ss.clarify_pending = None
            st.rerun()
        if cb.button("按默认口径继续", use_container_width=True):
            ss.result = run_pipeline(
                ss.clarify_pending["question"],
                ask=lambda _q: "默认口径",
                real=ss.clarify_pending["real"],
            )
            ss.clarify_pending = None
            st.rerun()

    # 场景快选的执行（在渲染循环中直接跑，结果写入 state）
    SCENE_QUESTIONS = {
        "sc_normal": "2018 年各州订单量排名？",
        "sc_clarify": "平台的销售额是多少？",
        "sc_retry": "2018 年各州订单量排名？",
        "sc_refuse": "把取消的订单从库里删掉",
    }
    if ss.pending_scene:
        extra = ss.pending_scene
        ss.result = run_pipeline(
            SCENE_QUESTIONS[extra["key"]],
            ask=(lambda _q: extra["canned"]) if extra.get("canned") else None,
            first_error=extra.get("first_error", False),
            real=False,
        )
        ss.pending_scene = None
        st.rerun()

    # 历史记录
    if ss.history:
        st.markdown('<div class="group-h">本轮记录</div>', unsafe_allow_html=True)
        rows = "".join(
            f"<tr><td>{h['q'][:22]}</td><td>{h['path']}</td>"
            f"<td>{'重试' + str(h['retries']) if h['retries'] else '—'}</td>"
            f"<td>{h['ms']:.0f}ms</td></tr>"
            for h in reversed(ss.history[-5:])
        )
        st.markdown(
            f'<table class="hist"><tr><th>问题</th><th>路径</th><th>重试</th><th>耗时</th></tr>{rows}</table>',
            unsafe_allow_html=True,
        )

# ================= 右栏：管线过程与结果 =================
with right:
    res = ss.result
    if not res:
        st.markdown(
            '<div class="empty">左侧选择一个演示场景或输入问题，这里展示'
            "「理解 → 检索 → 生成 → 执行 → 解读 → 答复」的完整管线过程、"
            "生成 SQL、结果表与守卫 / 重试细节。</div>",
            unsafe_allow_html=True,
        )
    else:
        t, f = res["trace"], res["final"]
        path = f.get("path", "normal")
        retries = len(t.get("retries") or [])
        pill_cls, pill_txt = PATH_PILL.get(path, ("y", path))

        # 路径横幅
        if path == "refuse":
            st.markdown(
                f"""<div class="banner red"><div class="t">变更类请求 · 已拒答
                <span class="pill r">{pill_txt}</span></div>
                命中词「{f.get("refuse_hit") or t.get("clarify", {}).get("answer", "")}」——
                只读分析助手不执行任何数据变更，问题已在理解层终止，未进入生成与执行。</div>""",
                unsafe_allow_html=True,
            )
        elif retries:
            st.markdown(
                f"""<div class="banner amber"><div class="t">执行报错 · 环节级重跑成功
                <span class="pill y">重试 {retries} 次</span></div>
                首轮 SQL 引用错误表名 → 错误与类型回填生成节点 → 修正后执行成功。</div>""",
                unsafe_allow_html=True,
            )

        # 管线节点流
        nodes = t.get("nodes") or []
        flow = [
            '<div class="panel"><h4>管线过程'
            f'（总耗时 ≈ {res["wall_ms"]:.0f} ms）</h4><div class="flow">'
        ]
        seen_understand = False
        for n in nodes:
            name = n["name"]
            loop = ""
            if name == "understand":
                if seen_understand:
                    loop = '<span class="lp">回路</span>'
                seen_understand = True
            flow.append(
                f'<div class="node {"loop" if loop else ""}">{loop}'
                f'<div class="nm"><span class="st-dot" style="background:var(--ok)"></span>'
                f"{NODE_LABELS.get(name, name)}</div>"
                f'<div class="ms">{n.get("ms", 0):.1f} ms</div></div>'
            )
        flow.append("</div></div>")
        st.markdown("".join(flow), unsafe_allow_html=True)

        # 澄清信息
        if t.get("clarify", {}).get("fired"):
            cl = t["clarify"]
            st.markdown(
                f"""<div class="banner blue"><div class="t">口径澄清
                <span class="pill g">歧义组 {cl["group"]}</span></div>
                追问：{cl["question"]}<br>回答：{cl["answer"]}</div>""",
                unsafe_allow_html=True,
            )

        # SQL 与结果
        sql = f.get("sql", "")
        rows = f.get("rows") or []
        cols = f.get("columns") or []
        if sql:
            st.markdown(
                f'<div class="panel"><h4>生成 SQL</h4><div class="sqlbox">{sql}</div></div>',
                unsafe_allow_html=True,
            )
        if rows:
            qr = quick_read(cols, rows)
            if qr:
                st.markdown(
                    f"""<div class="banner blue" style="margin-bottom:10px;">
                    <div class="t">结果速读</div>{qr}</div>""",
                    unsafe_allow_html=True,
                )
            st.markdown(
                f'<div class="panel"><h4>执行结果（{len(rows)} 行）</h4>', unsafe_allow_html=True
            )
            if cols:
                data = {
                    c: [_fmt(r[i]) if _is_num(r[i]) else str(r[i]) for r in rows[:12]]
                    for i, c in enumerate(cols)
                }
            else:
                data = {"结果": [str(r)[:80] for r in rows[:12]]}
            n_show = min(len(rows), 12)
            st.dataframe(data, use_container_width=True, height=max(120, 46 + 35 * n_show))
            st.markdown("</div>", unsafe_allow_html=True)
        if f.get("answer"):
            st.markdown(
                f"""<div class="panel"><h4>解读回答</h4>
                <div class="kv">{fmt_answer(f["answer"])}</div></div>""",
                unsafe_allow_html=True,
            )
        if not sql and not rows and path != "refuse":
            st.markdown(
                '<div class="empty">本轮无 SQL 产出（见上方横幅说明）。</div>',
                unsafe_allow_html=True,
            )

        # trace 明细
        with st.expander("运行 trace（节点耗时 / 歧义 / 重试 / 口径命中）"):
            st.json(
                {
                    "path": path,
                    "timings": t.get("timings"),
                    "clarify": t.get("clarify"),
                    "retries": t.get("retries"),
                    "metric_hits": f.get("metric_hits"),
                    "rows_n": len(rows),
                    "trace_file": res.get("trace_file"),
                }
            )

        # 记录进历史（避免 rerun 重复追加）
        key = (f.get("sql", ""), res["wall_ms"])
        hist_keys = [h["ms"] for h in ss.history]
        if not hist_keys or hist_keys[-1] != res["wall_ms"]:
            ss.history.append(
                {
                    "q": t.get("question", ""),
                    "path": pill_txt,
                    "retries": retries,
                    "ms": res["wall_ms"],
                }
            )
