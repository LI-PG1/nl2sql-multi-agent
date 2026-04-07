"""拉取 data-agent 使用的公开数据集到 data/raw/。

数据集（全部为公开数据集；仓库不提交数据本体，仅保留本脚本）：
- olist    主业务库     Olist 巴西电商（9 表 / 约 155 万行）        CC BY-NC-SA 4.0
- uci      备选业务库   UCI Online Retail II（约 100 万行）         CC BY 4.0
- bird     跨库评测基准 BIRD Mini-Dev SQLite（11 库 / 500 题）      CC BY-SA 4.0
- cspider  中文评测基准 CSpider dev（1034 题 / 20 库）              CC BY-SA 4.0
- spider   库资源       Spider 1.0（166 库，CSpider 执行依赖）      CC BY-SA 4.0
- samples  轻量冒烟库   Sakila / Chinook / Northwind（16 / 11 / 9 表）
- archer   双语推理基准 Archer 公开版（10 库 / 中英各 518 题）      CC BY 4.0
- zh_text2sql 中文样本  魔搭「中文Text2SQL」（DuSQL 标注体系 500+100）Apache 2.0

用法：
    python scripts/fetch_data.py                # 拉取全部（已就绪的自动跳过）
    python scripts/fetch_data.py bird cspider   # 按需拉取指定数据集

说明：数据目录（data/raw/）不入库；各数据集的用途与校验记录详见 docs/数据集说明.md。
"""

from __future__ import annotations

import json
import pathlib
import shutil
import sys
import time
import urllib.request
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"


def _download(url: str, dest: pathlib.Path, tries: int = 3) -> pathlib.Path:
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  [跳过下载] {dest.name}（{dest.stat().st_size / 1e6:.1f}MB 已存在）")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    for i in range(tries):
        try:
            print(f"  [下载] {url}")
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
                shutil.copyfileobj(r, f, length=1024 * 256)
            tmp.replace(dest)
            print(f"  [完成] {dest.name}（{dest.stat().st_size / 1e6:.1f}MB）")
            return dest
        except Exception as e:  # 网络抖动重试
            print(f"  [重试 {i + 1}/{tries}] {e}")
            time.sleep(3 + i * 2)
    raise RuntimeError(f"下载失败: {url}")


def _extract(zip_path: pathlib.Path, out_dir: pathlib.Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(out_dir)


def _done(marker: pathlib.Path) -> bool:
    return marker.exists()


# ------------------------------------------------------------------ 各数据集


def fetch_olist() -> None:
    """主业务库：Olist 巴西电商。CC BY-NC-SA 4.0（仅限非商业使用）。"""
    if _done(RAW / "olist" / "olist_customers_dataset.csv"):
        print("olist: 已就绪，跳过")
        return
    zip_path = _download(
        "https://www.kaggle.com/api/v1/datasets/download/olistbr/brazilian-ecommerce",
        RAW / "kaggle_olist.zip",
    )
    _extract(zip_path, RAW / "olist")
    print("olist: 完成 ->", RAW / "olist")


def fetch_uci() -> None:
    """备选业务库：UCI Online Retail II。CC BY 4.0。"""
    if _done(RAW / "uci" / "online_retail_II.xlsx"):
        print("uci: 已就绪，跳过")
        return
    zip_path = _download(
        "https://archive.ics.uci.edu/static/public/502/online+retail+ii.zip",
        RAW / "uci_retail_ii.zip",
    )
    _extract(zip_path, RAW / "uci")
    print("uci: 完成 ->", RAW / "uci")


def fetch_bird() -> None:
    """跨库评测基准：BIRD Mini-Dev（11 库 / 500 题，含 evidence 与难度）。CC BY-SA 4.0。"""
    if _done(RAW / "bird_mini_dev" / "mini_dev_sqlite.json"):
        print("bird: 已就绪，跳过")
        return
    zip_path = _download(
        "https://www.kaggle.com/api/v1/datasets/download/adithyagiri/bird-mini-dev-sqlite",
        RAW / "kaggle_bird_mini_dev.zip",
    )
    _extract(zip_path, RAW / "bird_mini_dev")
    print("bird: 完成 ->", RAW / "bird_mini_dev")


def fetch_cspider() -> None:
    """中文 NL2SQL 评测基准：CSpider dev（1034 题 / 20 库）。CC BY-SA 4.0。"""
    if _done(RAW / "cspider" / "dev.json"):
        print("cspider: 已就绪，跳过")
        return
    zip_path = _download(
        "https://www.kaggle.com/api/v1/datasets/download/qwqtat/cspider",
        RAW / "kaggle_cspider.zip",
    )
    _extract(zip_path, RAW / "cspider")
    print("cspider: 完成 ->", RAW / "cspider")


def fetch_spider() -> None:
    """Spider 1.0 库资源（166 库；CSpider 执行依赖，另含英文 train/dev）。CC BY-SA 4.0。"""
    if _done(RAW / "spider" / "database"):
        print("spider: 已就绪，跳过")
        return
    zip_path = _download(
        "https://www.kaggle.com/api/v1/datasets/download/jeromeblanchet/yale-universitys-spider-10-nlp-dataset",
        RAW / "kaggle_spider.zip",
    )
    _extract(zip_path, RAW / "spider")
    # 该 zip 内层自带 spider/ 前缀，拍平到 data/raw/spider/
    nested = RAW / "spider" / "spider"
    if nested.exists():
        for item in list(nested.iterdir()):
            target = RAW / "spider" / item.name
            if not target.exists():
                shutil.move(str(item), str(target))
        nested.rmdir()
    print("spider: 完成 ->", RAW / "spider")


def fetch_samples() -> None:
    """轻量冒烟库：Sakila / Chinook / Northwind（多库 schema 测试用）。"""
    sd = RAW / "sample_dbs"
    if _done(sd / "sakila.db") and _done(sd / "Chinook.db") and _done(sd / "Northwind.db"):
        print("samples: 已就绪，跳过")
        return
    sd.mkdir(parents=True, exist_ok=True)
    # Northwind + Chinook（同一包）
    z1 = _download(
        "https://www.kaggle.com/api/v1/datasets/download/rcurioso/northwind-and-chinook-database",
        RAW / "kaggle_northwind_chinook.zip",
    )
    _extract(z1, sd)
    # Sakila（从包内提取 sqlite 库文件）
    z2 = _download(
        "https://www.kaggle.com/api/v1/datasets/download/atanaskanev/sqlite-sakila-sample-database",
        RAW / "kaggle_sakila.zip",
    )
    _extract(z2, RAW / "sakila")
    src = RAW / "sakila" / "sqlite-sakila.db"
    if src.exists() and not (sd / "sakila.db").exists():
        shutil.copy2(src, sd / "sakila.db")
    print("samples: 完成 ->", sd)


def fetch_archer() -> None:
    """双语复杂推理基准：Archer 公开版（10 库 / 中英各 518 题 / 259 条 SQL）。CC BY 4.0。"""
    ar = RAW / "archer"
    if (
        _done(ar / "en_data" / "dev.json")
        and _done(ar / "zh_data" / "dev.json")
        and _done(ar / "database")
    ):
        print("archer: 已就绪，跳过")
        return
    ar.mkdir(parents=True, exist_ok=True)
    for name, out in [
        ("database.zip", "database"),
        ("en_data.zip", "en_data"),
        ("zh_data.zip", "zh_data"),
    ]:
        z = _download(f"https://sig4kg.github.io/archer-bench/dataset/{name}", ar / name)
        _extract(z, ar / out)
    print("archer: 完成 ->", ar)


def fetch_zh_text2sql() -> None:
    """中文 Text2SQL 样本：魔搭官方「中文Text2SQL数据集」（DuSQL 标注体系，500 训 + 100 测）。
    该镜像为标注样本（不含数据库本体），用于中文题面与标注结构（schema_link / header_knowledge）参考。
    许可：Apache License 2.0。"""
    ct = RAW / "chinese_text2sql"
    if _done(ct / "train") and _done(ct / "test"):
        print("zh_text2sql: 已就绪，跳过")
        return
    ct.mkdir(parents=True, exist_ok=True)
    # 魔搭开放接口：文件列表（返回带签名的临时直链）
    url = (
        "https://modelscope.cn/api/v1/datasets/modelscope/ChineseText2SQL"
        "/oss/tree?Revision=master&Recursive=true"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        items = json.load(r).get("Data") or []
    for it in items:
        name = pathlib.Path(it["Path"]).name  # train.zip / test.zip
        out = ct / name.replace(".zip", "")
        z = _download(it["Url"], ct / name)
        _extract(z, out)
        # 兼容包内 train/ 前缀：拍平一层
        nested = out / out.name
        if nested.is_dir():
            for item in list(nested.iterdir()):
                target = out / item.name
                if not target.exists():
                    shutil.move(str(item), str(target))
            shutil.rmtree(nested)
    print("zh_text2sql: 完成 ->", ct)


FETCHERS = {
    "olist": fetch_olist,
    "uci": fetch_uci,
    "bird": fetch_bird,
    "cspider": fetch_cspider,
    "spider": fetch_spider,
    "samples": fetch_samples,
    "archer": fetch_archer,
    "zh_text2sql": fetch_zh_text2sql,
}


def main() -> None:
    names = sys.argv[1:] or list(FETCHERS)
    unknown = [n for n in names if n not in FETCHERS]
    if unknown:
        print(f"未知数据集: {unknown}；可选: {list(FETCHERS)}")
        sys.exit(1)
    for name in names:
        print(f"\n[{name}]")
        FETCHERS[name]()
    print("\n全部完成。数据位于 data/raw/（不入库）；用途与许可见 docs/数据集说明.md。")


if __name__ == "__main__":
    main()
