#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""提示词词库：MySQL 存储 + SiliconFlow Embedding/Rerank 向量检索。

用途：把 GitHub/网上收集的热门图像提示词分类存入 MySQL，
生成图片时用向量模型检索最相近的提示词，作为参考喂给提示词翻译官。

依赖（可选，仅在启用词库时需要）：pymysql、numpy
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

CONFIG_DIR = Path.home() / ".deepseek-imagegen"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "use_in_translator": True,
    "top_k": 50,
    "final_k": 8,
    "embedding": {
        "base_url": "https://api.siliconflow.com/v1/embeddings",
        "api_key": "",
        "model": "Qwen/Qwen3-Embedding-8B",
    },
    "rerank": {
        "enabled": True,
        "base_url": "https://api.siliconflow.com/v1/rerank",
        "api_key": "",
        "model": "Qwen/Qwen3-Reranker-8B",
    },
    "mysql": {
        "host": "127.0.0.1",
        "port": 3306,
        "user": "",
        "password": "",
        "db": "prompt_library",
    },
}


class LibError(Exception):
    """词库功能错误，中文提示。"""


def load_config() -> dict[str, Any]:
    cfg: dict[str, Any] = {}
    if CONFIG_FILE.is_file():
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raise LibError("配置文件损坏：" + str(CONFIG_FILE))
    pl = cfg.get("prompt_library")
    if not isinstance(pl, dict):
        pl = {}
    merged = {k: (dict(DEFAULTS[k]) if isinstance(DEFAULTS[k], dict) else DEFAULTS[k]) for k in DEFAULTS}
    for k, v in pl.items():
        if k in DEFAULTS and isinstance(DEFAULTS[k], dict) and isinstance(v, dict):
            merged[k].update(v)
        else:
            merged[k] = v
    return merged


def mysql_conn(pl: dict[str, Any]):
    try:
        import pymysql
    except ImportError:
        raise LibError("缺少 pymysql 库，请先运行：pip install pymysql")
    m = pl.get("mysql") or {}
    if not str(m.get("user") or "").strip():
        raise LibError(
            "尚未配置 MySQL 连接：请在设置页的「提示词词库」里填写数据库账号密码，"
            "或用 Navicat 建好空库 prompt_library 后告知我。"
        )
    try:
        return pymysql.connect(
            host=str(m.get("host") or "127.0.0.1"),
            port=int(m.get("port") or 3306),
            user=str(m.get("user") or ""),
            password=str(m.get("password") or ""),
            database=str(m.get("db") or "prompt_library"),
            charset="utf8mb4",
            autocommit=True,
            connect_timeout=8,
        )
    except Exception as exc:
        raise LibError(f"MySQL 连接失败：{exc}（请检查账号密码和数据库是否已创建）")


def init_db(pl: dict[str, Any]) -> None:
    conn = mysql_conn(pl)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS prompts (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    content TEXT NOT NULL,
                    content_hash CHAR(40) NOT NULL,
                    lang VARCHAR(16) DEFAULT '',
                    category VARCHAR(64) DEFAULT '',
                    tags VARCHAR(512) DEFAULT '',
                    source VARCHAR(255) DEFAULT '',
                    source_url VARCHAR(512) DEFAULT '',
                    embedding LONGBLOB,
                    notes VARCHAR(512) DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY uk_content_hash (content_hash)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
    finally:
        conn.close()


def _http_json(url: str, payload: dict[str, Any], api_key: str, timeout: int = 120) -> dict[str, Any]:
    """带重试的 OpenAI 兼容 JSON POST（429 自动等待 8s/16s）。"""
    last_err: Optional[str] = None
    for attempt, delay in ((1, 8), (2, 16), (3, 0)):
        req = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": "Bearer " + api_key,
                "Content-Type": "application/json",
                "User-Agent": "deepseek-imagegen/prompt-lib",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, dict) and data.get("error"):
                err = data["error"]
                msg = err.get("message") if isinstance(err, dict) else str(err)
                raise LibError("接口返回错误：" + str(msg))
            return data
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:300]
            if exc.code == 429 and attempt < 3:
                time.sleep(delay)
                continue
            raise LibError(f"接口 HTTP {exc.code}：{body}")
        except urllib.error.URLError as exc:
            last_err = f"网络错误：{exc.reason}"
            if attempt < 3:
                time.sleep(delay)
                continue
            raise LibError(last_err)
        except LibError:
            raise
        except Exception as exc:
            last_err = str(exc)
            if attempt < 3:
                time.sleep(delay)
                continue
            raise LibError(last_err)
    raise LibError(last_err or "请求失败")


def embed_texts(pl: dict[str, Any], texts: list[str], input_type: str = "") -> list[list[float]]:
    """调用 SiliconFlow Embedding 接口，返回向量列表。"""
    if not texts:
        return []
    e = pl.get("embedding") or {}
    api_key = str(e.get("api_key") or "").strip()
    if not api_key:
        raise LibError("尚未配置 Embedding 密钥：请在设置页的「提示词词库」里填写。")
    base_url = str(e.get("base_url") or DEFAULTS["embedding"]["base_url"]).strip().rstrip("/")
    if not base_url.endswith("/embeddings"):
        base_url += "/embeddings"
    model = str(e.get("model") or DEFAULTS["embedding"]["model"])
    vectors: list[list[float]] = []
    batch = 16
    for start in range(0, len(texts), batch):
        chunk = texts[start : start + batch]
        payload: dict[str, Any] = {"model": model, "input": chunk}
        if input_type:
            payload["input_type"] = input_type
        try:
            data = _http_json(base_url, payload, api_key)
        except LibError as exc:
            if input_type and "input_type" in str(exc):
                payload.pop("input_type", None)
                data = _http_json(base_url, payload, api_key)
            else:
                raise
        items = data.get("data") or []
        if len(items) != len(chunk):
            raise LibError(f"Embedding 返回数量不符：期望 {len(chunk)}，实际 {len(items)}")
        for item in items:
            vec = item.get("embedding")
            if not vec:
                raise LibError("Embedding 返回缺少向量数据")
            vectors.append([float(x) for x in vec])
        if start + batch < len(texts):
            time.sleep(0.3)
    return vectors


def rerank_docs(pl: dict[str, Any], query: str, documents: list[str], top_n: int) -> list[int]:
    """调用 SiliconFlow Rerank 接口，返回按相关度排序的文档下标（取 top_n）。"""
    r = pl.get("rerank") or {}
    if not r.get("enabled"):
        return list(range(len(documents)))
    api_key = str(r.get("api_key") or "").strip()
    if not api_key or not documents:
        return list(range(len(documents)))
    base_url = str(r.get("base_url") or DEFAULTS["rerank"]["base_url"]).strip().rstrip("/")
    if not base_url.endswith("/rerank"):
        base_url += "/rerank"
    model = str(r.get("model") or DEFAULTS["rerank"]["model"])
    payload = {
        "model": model,
        "query": query,
        "documents": documents,
        "top_n": min(top_n, len(documents)),
    }
    data = _http_json(base_url, payload, api_key)
    results = data.get("results") or []
    ordered = sorted(
        ((int(x.get("index", -1)), float(x.get("relevance_score") or 0.0)) for x in results if isinstance(x, dict)),
        key=lambda pair: pair[1],
        reverse=True,
    )
    return [idx for idx, _ in ordered if 0 <= idx < len(documents)]


def _cosine_top_k(query_vec: list[float], vectors: list[list[float]], k: int) -> list[int]:
    import numpy as np

    q = np.asarray(query_vec, dtype=np.float32)
    qn = np.linalg.norm(q)
    if qn == 0:
        return list(range(min(k, len(vectors))))
    q = q / qn
    mat = np.asarray(vectors, dtype=np.float32)
    if mat.size == 0:
        return []
    mat = mat / np.maximum(np.linalg.norm(mat, axis=1, keepdims=True), 1e-12)
    scores = mat @ q
    order = np.argsort(-scores)[:k].tolist()
    return [int(i) for i in order]


def _load_vectors(cur, ids: list[int]) -> dict[int, list[float]]:
    out: dict[int, list[float]] = {}
    if not ids:
        return out
    fmt = ",".join(["%s"] * len(ids))
    cur.execute(f"SELECT id, embedding FROM prompts WHERE id IN ({fmt})", ids)
    for rid, blob in cur.fetchall():
        if blob:
            import struct

            raw = bytes(blob)
            n = len(raw) // 4
            out[int(rid)] = list(struct.unpack(f"<{n}f", raw))
    return out


def search(pl: dict[str, Any], query: str, top_k: Optional[int] = None, final_k: Optional[int] = None) -> list[dict[str, Any]]:
    """向量检索：需求 → 向量 → 余弦相似度取 top_k → Rerank 精排取 final_k。"""
    top_k = int(top_k or pl.get("top_k") or 50)
    final_k = int(final_k or pl.get("final_k") or 8)
    if not str(query or "").strip():
        raise LibError("检索关键词不能为空。")
    conn = mysql_conn(pl)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM prompts")
            count = int(cur.fetchone()[0])
            if count == 0:
                raise LibError("词库还是空的：请先用 prompt_lib.py import 导入提示词。")
            query_vec = embed_texts(pl, [query], input_type="query")[0]
            cur.execute("SELECT id, content, category, tags, source FROM prompts")
            rows = cur.fetchall()
            ids = [int(r[0]) for r in rows]
            vectors = _load_vectors(cur, ids)
            order = _cosine_top_k(query_vec, [vectors[i] for i in ids if i in vectors], min(top_k, count))
            docs = [str(rows[i][1]) for i in order]
            if pl.get("rerank", {}).get("enabled"):
                try:
                    order = [order[i] for i in rerank_docs(pl, query, docs, final_k)]
                except LibError:
                    order = order[:final_k]
            else:
                order = order[:final_k]
            results = []
            for i in order:
                rid, content, category, tags, source = rows[i]
                results.append(
                    {
                        "id": int(rid),
                        "content": str(content),
                        "category": str(category or ""),
                        "tags": str(tags or ""),
                        "source": str(source or ""),
                    }
                )
            return results
    finally:
        conn.close()


def _sha1(text: str) -> str:
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()


def import_prompts(
    pl: dict[str, Any],
    items: list[dict[str, Any]],
    *,
    default_category: str = "",
    default_tags: str = "",
    default_source: str = "",
    default_url: str = "",
) -> dict[str, int]:
    """导入提示词：去重后批量向量化并写入 MySQL。"""
    if not items:
        return {"total": 0, "inserted": 0, "skipped": 0}
    cleaned: list[dict[str, Any]] = []
    for it in items:
        content = str(it.get("content") or it.get("prompt") or it.get("text") or "").strip()
        if len(content) < 8:
            continue
        cleaned.append(
            {
                "content": content,
                "lang": str(it.get("lang") or "").strip()[:16],
                "category": str(it.get("category") or default_category).strip()[:64],
                "tags": str(it.get("tags") or default_tags).strip()[:512],
                "source": str(it.get("source") or default_source).strip()[:255],
                "source_url": str(it.get("source_url") or default_url).strip()[:512],
            }
        )
    if not cleaned:
        return {"total": len(items), "inserted": 0, "skipped": len(items)}
    conn = mysql_conn(pl)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT content_hash FROM prompts")
            existing = {str(row[0]) for row in cur.fetchall()}
        new_items = []
        seen_local: set[str] = set()
        for it in cleaned:
            h = _sha1(it["content"])
            if h in existing or h in seen_local:
                continue
            seen_local.add(h)
            new_items.append(it)
        skipped = len(cleaned) - len(new_items)
        if not new_items:
            return {"total": len(items), "inserted": 0, "skipped": skipped}
        vectors = embed_texts(pl, [it["content"] for it in new_items], input_type="document")
        import struct

        with conn.cursor() as cur:
            for it, vec in zip(new_items, vectors):
                blob = struct.pack(f"<{len(vec)}f", *vec)
                cur.execute(
                    """
                    INSERT INTO prompts
                        (content, content_hash, lang, category, tags, source, source_url, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        it["content"],
                        _sha1(it["content"]),
                        it["lang"],
                        it["category"],
                        it["tags"],
                        it["source"],
                        it["source_url"],
                        blob,
                    ),
                )
        return {"total": len(items), "inserted": len(new_items), "skipped": skipped}
    finally:
        conn.close()


def read_import_file(path: str) -> list[dict[str, Any]]:
    p = Path(path).expanduser()
    if not p.is_file():
        raise LibError(f"找不到导入文件：{p}")
    text = p.read_text(encoding="utf-8", errors="replace")
    suffix = p.suffix.lower()
    if suffix == ".json":
        data = json.loads(text)
        if isinstance(data, dict):
            for key in ("prompts", "data", "items"):
                if isinstance(data.get(key), list):
                    data = data[key]
                    break
        if not isinstance(data, list):
            raise LibError("JSON 文件应为数组或包含 prompts/data/items 数组的对象。")
        items: list[dict[str, Any]] = []
        for row in data:
            if isinstance(row, str):
                items.append({"content": row})
            elif isinstance(row, dict):
                items.append(row)
        return items
    if suffix == ".jsonl":
        items = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                items.append({"content": line})
                continue
            if isinstance(row, str):
                items.append({"content": row})
            elif isinstance(row, dict):
                items.append(row)
        return items
    if suffix == ".csv":
        import csv

        reader = csv.DictReader(__import__("io").StringIO(text))
        rows = list(reader)
        items = []
        for row in rows:
            content = str(row.get("content") or row.get("prompt") or row.get("text") or "").strip()
            if content:
                items.append(dict(row))
        return items
    raise LibError(f"不支持的导入格式：{suffix}（支持 .json / .jsonl / .csv）")


def stats(pl: dict[str, Any]) -> dict[str, Any]:
    conn = mysql_conn(pl)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM prompts")
            total = int(cur.fetchone()[0])
            cur.execute("SELECT category, COUNT(*) FROM prompts GROUP BY category ORDER BY COUNT(*) DESC")
            by_category = [{"category": str(r[0] or "未分类"), "count": int(r[1])} for r in cur.fetchall()]
        return {"total": total, "by_category": by_category}
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="prompt_lib", description="提示词词库：MySQL + 向量检索")
    sub = parser.add_subparsers(dest="command", required=True)
    imp = sub.add_parser("import", help="导入提示词文件（.json/.jsonl/.csv）")
    imp.add_argument("path", help="文件路径")
    imp.add_argument("--lang", default="", help="默认语言，如 zh / en")
    imp.add_argument("--category", default="", help="默认分类，如 插画 / 摄影 / 3D")
    imp.add_argument("--tags", default="", help="默认标签，逗号分隔")
    imp.add_argument("--source", default="", help="来源名称")
    imp.add_argument("--source-url", default="", help="来源链接")
    s = sub.add_parser("search", help="向量检索提示词")
    s.add_argument("query", help="需求描述")
    s.add_argument("--k", type=int, default=None, help="返回条数")
    s.add_argument("--json", action="store_true", help="机器可读输出")
    sub.add_parser("init", help="创建数据表")
    sub.add_parser("stats", help="词库统计")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        pl = load_config()
        if args.command == "init":
            init_db(pl)
            print("数据表已就绪。")
            return 0
        if args.command == "stats":
            result = stats(pl)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "import":
            items = read_import_file(args.path)
            result = import_prompts(
                pl,
                items,
                default_category=args.category,
                default_tags=args.tags,
                default_source=args.source,
                default_url=args.source_url,
            )
            print(
                f"导入完成：共 {result['total']} 条，新写入 {result['inserted']} 条，"
                f"跳过重复 {result['skipped']} 条。"
            )
            return 0
        if args.command == "search":
            results = search(pl, args.query, top_k=args.k)
            if args.json:
                print(json.dumps(results, ensure_ascii=False, indent=2))
            else:
                for r in results:
                    print(f"[{r['id']}] ({r.get('category') or '未分类'}) {r['content'][:120]}")
            return 0
    except LibError as exc:
        print("错误：" + str(exc), file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
