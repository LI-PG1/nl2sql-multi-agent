"""KDD DataSpace 公测任务适配：任务本地工作区 → 统一任务库 + 任务简报。

输入：data/raw/kdd_dataspace/bench/DataSpace-Benchmark
  - input/task_N/task.json（问题）+ context/（csv / json / sqlite / markdown / pdf / video）
  - output/task_N/gold.csv + evaluation/configs/task_N.json（仅 60 个公测参考包）
输出：eval/kdd_work/<task_id>/
  - task.db        统一 SQLite（原 sqlite 表 + CSV 表 + JSON 展平表；标识符安全化，映射见 manifest）
  - brief.md       任务简报（表清单 + knowledge.md + PDF 抽取文本 + 视频不可用声明 + 输出格式要求）
  - manifest.json  机器可读适配记录（表清单 / 行列数 / 跳过文件 / 模态）

设计口径（与 BIRD/CSpider/Archer 外测一致的"对未适配数据的公平性"）：
- 不做任何针对 DataSpace 的专用口径层 / few-shot；schema 全量注入；
- 标识符安全化只改"库内名"，原名→库内名映射写入简报，预测表头不参与判分（官方规则）；
- 视频模态本环境不可解析——简报中显式声明，不静默跳过。

用法：
    python eval/kdd_prepare.py            # 全量 60 个公测任务
    python eval/kdd_prepare.py --task task_10
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sqlite3
import sys

import pandas as pd
from pypdf import PdfReader

ROOT = pathlib.Path(__file__).resolve().parents[1]
BENCH = ROOT / "data" / "raw" / "kdd_dataspace" / "bench" / "DataSpace-Benchmark"
WORK = ROOT / "eval" / "kdd_work"

KNOWLEDGE_CAP = 16000  # knowledge.md 注入上限（字符）
PDF_CAP_PER_FILE = 4000  # 单个 PDF 抽取文本上限
DOC_MD_INLINE_CAP = 24000  # md 文档总量小于该值时全文内联进简报
DOC_SAMPLE_CHARS = 240  # 单个文档在简报中的样例字符数
CHUNK_SIZE = 1500  # doc_chunks 分块大小（字符）


def sanitize_ident(name: str) -> str:
    """标识符安全化：保留中英文与数字，其余转 _；数字开头加 t_ 前缀。"""
    s = re.sub(r"[^0-9A-Za-z_\u4e00-\u9fff]", "_", str(name)).strip("_")
    if not s:
        s = "col"
    if s[0].isdigit():
        s = "t_" + s
    return s


def unique_name(base: str, taken: set[str]) -> str:
    name, i = base, 2
    while name in taken:
        name = f"{base}_{i}"
        i += 1
    taken.add(name)
    return name


def table_names(con: sqlite3.Connection, schema: str = "main") -> list[str]:
    return [
        r[0]
        for r in con.execute(
            f"SELECT name FROM {schema}.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]


def load_csv(path: pathlib.Path) -> pd.DataFrame | None:
    try:
        return pd.read_csv(path)
    except Exception as e:  # noqa: BLE001
        print(f"    [warn] CSV 读取失败 {path.name}: {str(e)[:80]}")
        return None


def load_json(path: pathlib.Path) -> pd.DataFrame | None:
    """JSON → 表：list[dict] 直接用；dict 取其中的记录列表（records/data/rows/items 或
    首个 list[dict] 值）；其余（嵌套结构 / 非记录型）跳过。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"    [warn] JSON 解析失败 {path.name}: {str(e)[:80]}")
        return None
    records = None
    if isinstance(raw, list) and raw and all(isinstance(x, dict) for x in raw):
        records = raw
    elif isinstance(raw, dict):
        for key in ("records", "data", "rows", "items", "result"):
            v = raw.get(key)
            if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                records = v
                break
        if records is None:
            for v in raw.values():
                if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                    records = v
                    break
    if records is None:
        return None
    try:
        return pd.DataFrame(records)
    except Exception as e:  # noqa: BLE001
        print(f"    [warn] JSON 展平失败 {path.name}: {str(e)[:80]}")
        return None


def extract_pdf_text(path: pathlib.Path) -> str:
    try:
        reader = PdfReader(str(path))
        parts = [(p.extract_text() or "") for p in reader.pages[:10]]
        return "\n".join(parts).strip()
    except Exception as e:  # noqa: BLE001
        return f"（PDF 文本抽取失败：{type(e).__name__}: {str(e)[:80]}）"


def chunk_text(text: str, size: int) -> list[str]:
    """按段落边界聚合分块（超长段落按 size 硬切）。"""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, buf = [], ""
    for p in paras:
        while len(p) > size:
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.append(p[:size])
            p = p[size:]
        if len(buf) + len(p) + 2 <= size:
            buf = f"{buf}\n\n{p}" if buf else p
        else:
            if buf:
                chunks.append(buf)
            buf = p
    if buf:
        chunks.append(buf)
    return chunks


def prepare_task(task_id: str) -> dict:
    ctx = BENCH / "input" / task_id / "context"
    out_dir = WORK / task_id
    out_dir.mkdir(parents=True, exist_ok=True)
    db_path = out_dir / "task.db"
    man_path = out_dir / "manifest.json"
    need_build = True
    old: dict = {}
    if db_path.exists() and man_path.exists():
        try:
            old = json.loads(man_path.read_text(encoding="utf-8"))
            if old.get("adapt_version", 0) >= 3:
                return {**old, "skipped_rerun": True}
        except Exception:  # noqa: BLE001
            old = {}
    if (
        db_path.exists()
        and old.get("tables")
        and all(
            t.get("source", "").startswith(("db/", "csv/", "json/", "doc/")) for t in old["tables"]
        )
        and (
            (ctx / "db").exists()
            == any(t.get("source", "").startswith("db/") for t in old["tables"])
        )
    ):
        # 结构层已就绪（sqlite 修复后建过）——不重建、不删除，仅补文档层
        need_build = False

    con = sqlite3.connect(db_path)
    taken: set[str] = set()
    manifest = {
        "task_id": task_id,
        "adapt_version": 3,
        "tables": [],
        "skipped": [],
        "modalities": {},
    }

    # —— 1~3) 结构层：原 sqlite / CSV / JSON 入任务库（已就绪则复用旧清单） ——
    if need_build:
        for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            con.execute(f'DROP TABLE IF EXISTS "{t}"')  # 清空残留（不删文件）
        con.commit()
        src_dbs = sorted((ctx / "db").glob("*.sqlite")) if (ctx / "db").exists() else []
        for sdb in src_dbs:
            cur = con.cursor()
            cur.execute("ATTACH DATABASE ? AS src", (str(sdb),))
            names = [
                r[0]
                for r in cur.execute(
                    "SELECT name FROM src.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            ]
            for tname in names:
                t_san = unique_name(sanitize_ident(tname), taken)
                col_info = cur.execute(f'PRAGMA src.table_info("{tname}")').fetchall()
                col_defs = ", ".join(f'"{sanitize_ident(c[1])}" {c[2] or "TEXT"}' for c in col_info)
                cur.execute(f'CREATE TABLE main."{t_san}" ({col_defs})')
                sel = ", ".join(f'src."{tname}"."{c[1]}"' for c in col_info)
                cur.execute(f'INSERT INTO main."{t_san}" SELECT {sel} FROM src."{tname}"')
                n = cur.execute(f'SELECT COUNT(*) FROM main."{t_san}"').fetchone()[0]
                manifest["tables"].append(
                    {
                        "table": t_san,
                        "source": f"db/{sdb.name}:{tname}",
                        "rows": n,
                        "columns": [{"db": sanitize_ident(c[1]), "orig": c[1]} for c in col_info],
                    }
                )
            cur.close()
            con.commit()
            con.execute("DETACH DATABASE src")

        csv_dir = ctx / "csv"
        if csv_dir.exists():
            for f in sorted(csv_dir.glob("*.csv")):
                df = load_csv(f)
                if df is None or df.empty and len(df.columns) == 0:
                    manifest["skipped"].append({"file": f"csv/{f.name}", "why": "读取失败或空"})
                    continue
                t_san = unique_name(sanitize_ident(f.stem), taken)
                df.columns = [sanitize_ident(c) for c in df.columns]
                df.to_sql(t_san, con, if_exists="replace", index=False)
                manifest["tables"].append(
                    {
                        "table": t_san,
                        "source": f"csv/{f.name}",
                        "rows": int(len(df)),
                        "columns": [{"db": sanitize_ident(c), "orig": str(c)} for c in df.columns],
                    }
                )

        json_dir = ctx / "json"
        if json_dir.exists():
            for f in sorted(json_dir.glob("*.json")):
                df = load_json(f)
                if df is None or (df.empty and len(df.columns) == 0):
                    manifest["skipped"].append(
                        {"file": f"json/{f.name}", "why": "非表格结构或解析失败"}
                    )
                    continue
                t_san = unique_name(sanitize_ident(f.stem), taken)
                df.columns = [sanitize_ident(c) for c in df.columns]
                df.to_sql(t_san, con, if_exists="replace", index=False)
                manifest["tables"].append(
                    {
                        "table": t_san,
                        "source": f"json/{f.name}",
                        "rows": int(len(df)),
                        "columns": [{"db": sanitize_ident(c), "orig": str(c)} for c in df.columns],
                    }
                )
        con.commit()
    else:
        manifest["tables"] = old.get("tables", [])
        manifest["skipped"] = old.get("skipped", [])
        taken = {t["table"] for t in manifest["tables"]}

    # —— 4) 文档层：doc/*.md 与 doc/*.pdf 全文 → doc_chunks 表（可 SQL 检索） ——
    doc_dir = ctx / "doc"
    doc_files: list[tuple[str, str]] = []  # (name, full_text)
    if doc_dir.exists():
        for f in sorted(doc_dir.glob("*.md")):
            doc_files.append((f.name, f.read_text(encoding="utf-8", errors="ignore")))
        for f in sorted(doc_dir.glob("*.pdf")):
            doc_files.append((f.name, extract_pdf_text(f)))
    con.execute("DROP TABLE IF EXISTS doc_chunks")
    con.execute("CREATE TABLE doc_chunks (file TEXT, seq INTEGER, text TEXT)")
    n_chunks = 0
    for name, text in doc_files:
        for i, chunk in enumerate(chunk_text(text, CHUNK_SIZE)):
            con.execute("INSERT INTO doc_chunks VALUES (?, ?, ?)", (name, i + 1, chunk))
            n_chunks += 1
    con.commit()
    manifest["doc_files"] = [{"file": n, "chars": len(t)} for n, t in doc_files]
    manifest["doc_chunks"] = n_chunks
    manifest["tables"] = [t for t in manifest["tables"] if t["table"] != "doc_chunks"]
    if n_chunks:
        manifest["tables"].append(
            {
                "table": "doc_chunks",
                "source": "doc/（全文分块）",
                "rows": n_chunks,
                "columns": [
                    {"db": "file", "orig": "文件名"},
                    {"db": "seq", "orig": "块序"},
                    {"db": "text", "orig": "文本（约1500字符/块）"},
                ],
            }
        )

    # —— 5) 文本证据：knowledge.md ——
    know_text = ""
    kf = ctx / "knowledge.md"
    if kf.exists():
        know_text = kf.read_text(encoding="utf-8")[:KNOWLEDGE_CAP]

    # —— 6) 模态与视频声明 ——
    videos = sorted(p.name for p in (ctx / "video").glob("*")) if (ctx / "video").exists() else []
    src_dbs_n = len(list((ctx / "db").glob("*.sqlite"))) if (ctx / "db").exists() else 0
    manifest["modalities"] = {
        "csv": len(list((ctx / "csv").glob("*.csv"))) if (ctx / "csv").exists() else 0,
        "json": len(list((ctx / "json").glob("*.json"))) if (ctx / "json").exists() else 0,
        "sqlite": src_dbs_n,
        "markdown": 1 if kf.exists() else 0,
        "pdf": len(list(doc_dir.glob("*.pdf"))) if doc_dir.exists() else 0,
        "doc_md": len(list(doc_dir.glob("*.md"))) if doc_dir.exists() else 0,
        "video": len(videos),
        "video_files": videos,
    }

    # —— 7) 简报 ——
    lines = [
        "# 任务工作区简报（KDD DataSpace 适配层生成）",
        "",
        f"## 数据表（共 {len(manifest['tables'])} 张，已全部载入任务数据库）",
        "",
    ]
    for t in manifest["tables"]:
        cols = "、".join(
            f"{c['db']}（{c['orig']}）" if c["db"] != c["orig"] else c["db"]
            for c in t["columns"][:40]
        )
        lines.append(f"- `{t['table']}`（{t['rows']} 行，来源 {t['source']}）：{cols}")
    if manifest["doc_files"]:
        lines += [
            "",
            f"## 文档资料（doc/，共 {len(manifest['doc_files'])} 份；"
            "全文已分块载入 `doc_chunks` 表——可用 SQL LIKE 检索，如 "
            "`SELECT text FROM doc_chunks WHERE file='x.md' AND text LIKE '%关键词%'`）",
            "",
            "注意：文档文件名与数据表名对应；若 knowledge.md 中提及的表不在数据库内，"
            "其数据通常以同名文档形式提供（此时请检索 doc_chunks 取原文）。",
            "",
        ]
        for d in manifest["doc_files"]:
            lines.append(f"- {d['file']}（{d['chars']} 字符）")
        total_md = sum(len(t) for n, t in doc_files if n.endswith(".md"))
        if total_md and total_md <= DOC_MD_INLINE_CAP:
            for name, text in doc_files:
                if name.endswith(".md"):
                    lines += ["", f"### 文档全文：{name}", "", text]
        else:
            for name, text in doc_files:
                lines += [
                    "",
                    f"### 文档样例（前 {DOC_SAMPLE_CHARS} 字符）：{name}",
                    "",
                    text[:DOC_SAMPLE_CHARS] + "……",
                ]
    if know_text:
        lines += ["", "## 知识文档（knowledge.md）", "", know_text]
    if videos:
        lines += [
            "",
            "## 不可用输入",
            "",
            f"- 视频文件：{'、'.join(videos)}——本环境不具备视频理解能力，视频中展示的信息不可用；"
            "若题面要求以视频内容为准，请依据其余可解析材料尽力作答并在答案中说明。",
        ]
    if manifest["skipped"]:
        lines += ["", "## 未能结构化的文件", ""]
        for s in manifest["skipped"]:
            lines.append(f"- {s['file']}：{s['why']}")
    lines += [
        "",
        "## 输出格式要求",
        "",
        "- 查询结果必须直接构成最终答案表：包含题面要求的全部列与全部行，表头一行 + 数据行；",
        "- 数值严格遵循题面要求（四舍五入位数、删除末尾多余的 0、单位、百分比）；",
        "- 排序遵循题面；题面未要求排序时不限；",
        "- 表名 / 列名含中文或特殊字符（括号、百分号、空格等）时，SQL 中必须用双引号包裹标识符。",
    ]
    (out_dir / "brief.md").write_text("\n".join(lines), encoding="utf-8")
    manifest["brief_chars"] = len("\n".join(lines))
    man_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    con.close()
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default="", help="只准备指定任务（如 task_10）")
    args = ap.parse_args()

    ids = (
        [args.task]
        if args.task
        else json.loads(
            (BENCH.parent.parent / "public_reference_tasks.json").read_text(encoding="utf-8")
        )["task_ids"]
    )
    WORK.mkdir(parents=True, exist_ok=True)
    for i, tid in enumerate(ids, 1):
        m = prepare_task(tid)
        tag = (
            "跳过（已就绪）"
            if m.get("skipped_rerun")
            else f"表 {len(m['tables'])}｜文档 {len(m.get('doc_files', []))} 份/{m.get('doc_chunks', 0)} 块"
            f"｜简报 {m['brief_chars']} 字符｜视频 {m['modalities']['video']} 个"
        )
        print(f"[{i:02d}/{len(ids)}] {tid}｜{tag}", flush=True)
    print(f"[kdd_prepare] 完成 → {WORK}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
