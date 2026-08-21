"""提示词词库：MySQL 存储 + SiliconFlow Embedding/Rerank 向量检索（archived 条目不参与检索）。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import struct
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

from .config import default_config_path, load_config as load_user_config


class LibError(Exception):
    """词库功能错误，中文提示。"""


DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "use_in_translator": True,
    "top_k": 30,
    "final_k": 6,
    "categories": [],
    "priority_category": "自家精品",
    "priority_count": 3,
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


def load_config() -> dict[str, Any]:
    """读取词库配置段并合并默认值。"""
    cfg = load_user_config()
    pl = cfg.get("prompt_library")
    if not isinstance(pl, dict):
        pl = {}
    merged: dict[str, Any] = {}
    for k, default in DEFAULTS.items():
        if isinstance(default, dict):
            item = dict(default)
            if isinstance(pl.get(k), dict):
                item.update(pl[k])
            merged[k] = item
        else:
            merged[k] = pl.get(k, default)
    return merged


def mysql_conn(pl: dict[str, Any]):
    """连接 MySQL（pymysql 可选依赖）。"""
    try:
        import pymysql
    except ImportError:
        raise LibError("缺少 pymysql 库，请先运行：pip install pymysql")
    m = pl.get("mysql") or {}
    if not str(m.get("user") or "").strip():
        raise LibError(
            "尚未配置 MySQL 连接：请在 config.json 的 prompt_library.mysql 里填写数据库账号密码。"
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
    """建表（含 archived 列与分类索引）；老库自动补列。"""
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
                    archived TINYINT NOT NULL DEFAULT 0,
                    UNIQUE KEY uk_content_hash (content_hash),
                    KEY idx_prompts_category (category)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            for col, ddl in (
                ("requirement_embedding", "ALTER TABLE prompts ADD COLUMN requirement_embedding LONGBLOB NULL"),
                ("archived", "ALTER TABLE prompts ADD COLUMN archived TINYINT NOT NULL DEFAULT 0"),
            ):
                cur.execute(
                    "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
                    "AND TABLE_NAME='prompts' AND COLUMN_NAME=%s",
                    (col,),
                )
                if int(cur.fetchone()[0]) == 0:
                    cur.execute(ddl)
            cur.execute(
                "SELECT COUNT(*) FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() "
                "AND TABLE_NAME='prompts' AND INDEX_NAME='idx_prompts_category'"
            )
            if int(cur.fetchone()[0]) == 0:
                cur.execute("ALTER TABLE prompts ADD INDEX idx_prompts_category (category)")
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
    """调用 SiliconFlow Embedding，返回向量列表。"""
    if not texts:
        return []
    e = pl.get("embedding") or {}
    api_key = str(e.get("api_key") or "").strip()
    if not api_key:
        raise LibError("尚未配置 Embedding 密钥：请在 config.json 的 prompt_library.embedding 里填写。")
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
    """SiliconFlow Rerank；未配置/异常时按原顺序返回。"""
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
        (
            (int(x.get("index", -1)), float(x.get("relevance_score") or 0.0))
            for x in results
            if isinstance(x, dict)
        ),
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


def _pick_vec(embedding_blob: Any, requirement_blob: Any) -> list[float]:
    """优先使用“原始需求向量”（口语对口语最准），没有则用提示词向量。"""
    blob = requirement_blob or embedding_blob
    if not blob:
        return []
    raw = bytes(blob)
    n = len(raw) // 4
    return list(struct.unpack(f"<{n}f", raw))


def search(
    pl: dict[str, Any],
    query: str,
    top_k: Optional[int] = None,
    final_k: Optional[int] = None,
    categories: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """向量检索：只检索 archived=0 的活跃词条，优先分类照旧混入。"""
    top_k = int(top_k or pl.get("top_k") or 30)
    final_k = int(final_k or pl.get("final_k") or 6)
    if not str(query or "").strip():
        raise LibError("检索关键词不能为空。")
    cats = [str(c).strip() for c in (categories or pl.get("categories") or []) if str(c).strip()]
    priority = str(pl.get("priority_category") or "").strip()
    priority_count = max(0, int(pl.get("priority_count") or 3))
    conn = mysql_conn(pl)
    try:
        with conn.cursor() as cur:
            where = " WHERE archived=0"
            params: list[Any] = []
            if cats:
                where += " AND category IN (" + ",".join(["%s"] * len(cats)) + ")"
                params = cats
            cur.execute("SELECT COUNT(*) FROM prompts" + where, params)
            count = int(cur.fetchone()[0])
            if count == 0:
                raise LibError(
                    "词库（或所选分类）里没有活跃提示词：请先用 prompt_lib.py import / add 导入。"
                )
            query_vec = embed_texts(pl, [query], input_type="query")[0]
            cur.execute(
                "SELECT id, content, category, tags, source, embedding, requirement_embedding"
                " FROM prompts" + where,
                params,
            )
            rows = cur.fetchall()
            ids = [int(r[0]) for r in rows]
            vectors = [_pick_vec(r[5], r[6]) for r in rows]
            order = _cosine_top_k(query_vec, vectors, min(top_k, len(ids)))
            candidates = [(ids[i], rows[i]) for i in order]
            seen = set(ids[i] for i in order)
            prio_cands: list[tuple[int, tuple[Any, ...]]] = []
            if priority and priority_count > 0:
                cur.execute(
                    "SELECT id, content, category, tags, source, embedding, requirement_embedding"
                    " FROM prompts WHERE archived=0 AND category=%s ORDER BY id DESC LIMIT %s",
                    (priority, priority_count * 3),
                )
                for prow in cur.fetchall():
                    pid = int(prow[0])
                    if pid in seen:
                        continue
                    seen.add(pid)
                    prio_cands.append((pid, prow))
            pool = candidates + prio_cands
            prio_ids = {pid for pid, _ in prio_cands}
            docs = [str(r[1]) for _, r in pool]
            if pl.get("rerank", {}).get("enabled") and len(docs) > final_k:
                try:
                    pool_order = [pool[i] for i in rerank_docs(pl, query, docs, len(docs))]
                except LibError:
                    pool_order = pool
            else:
                pool_order = pool
            prio_pool = [(pid, row) for pid, row in pool_order if pid in prio_ids][:priority_count]
            others = [(pid, row) for pid, row in pool_order if pid not in prio_ids]
            final_order = (prio_pool + others)[:final_k]
            results = []
            for rid, row in final_order:
                _rid, content, category, tags, source = row[:5]
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


def add_prompt(
    pl: dict[str, Any],
    content: str,
    *,
    category: str = "自家精品",
    tags: str = "",
    requirement: str = "",
    source: str = "",
    source_url: str = "",
    notes: str = "",
) -> dict[str, Any]:
    """单条入库：正文 + 可选原始需求（双向量），自动去重。"""
    content = str(content or "").strip()
    if len(content) < 8:
        raise LibError("提示词内容太短（至少 8 个字）。")
    req = str(requirement or "").strip()
    conn = mysql_conn(pl)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM prompts WHERE content_hash=%s", (_sha1(content),))
            if cur.fetchone():
                return {"added": False, "reason": "已存在相同内容的提示词"}
            texts = [content]
            if req:
                texts.append(req)
            vectors = embed_texts(pl, texts, input_type="document")
            content_vec = vectors[0]
            req_vec = vectors[1] if len(vectors) > 1 else None
            blob = struct.pack(f"<{len(content_vec)}f", *content_vec)
            req_blob = struct.pack(f"<{len(req_vec)}f", *req_vec) if req_vec is not None else None
            cur.execute(
                """
                INSERT INTO prompts
                    (content, content_hash, lang, category, tags, source, source_url, embedding,
                     requirement_embedding, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    content,
                    _sha1(content),
                    "zh",
                    str(category or "").strip()[:64] or "未分类",
                    str(tags or "").strip()[:512],
                    str(source or "").strip()[:255],
                    str(source_url or "").strip()[:512],
                    blob,
                    req_blob,
                    str(notes or "").strip()[:512],
                ),
            )
            pid = int(cur.lastrowid)
        return {"added": True, "id": pid, "vectors": 2 if req_vec is not None else 1}
    finally:
        conn.close()


def backup(pl: dict[str, Any], out_path: str = "") -> dict[str, Any]:
    """把全部提示词导出为 JSONL 备份。"""
    conn = mysql_conn(pl)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT content, lang, category, tags, source, source_url, notes"
                " FROM prompts ORDER BY id"
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    if not out_path:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        out_path = str(default_config_path().parent / "backup" / f"prompts-{stamp}.jsonl")
    out = Path(out_path).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        for r in rows:
            handle.write(
                json.dumps(
                    {
                        "content": r[0],
                        "lang": r[1],
                        "category": r[2],
                        "tags": r[3],
                        "source": r[4],
                        "source_url": r[5],
                        "notes": r[6],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    return {"count": len(rows), "path": str(out_path)}


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
    """读取导入文件（.json / .jsonl / .csv）。"""
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
        return [row if isinstance(row, dict) else {"content": row} for row in data]
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
            items.append(row if isinstance(row, dict) else {"content": row})
        return items
    if suffix == ".csv":
        reader = csv.DictReader(io.StringIO(text))
        return [dict(row) for row in reader if str(row.get("content") or row.get("prompt") or row.get("text") or "").strip()]
    raise LibError(f"不支持的导入格式：{suffix}（支持 .json / .jsonl / .csv）")


def stats(pl: dict[str, Any]) -> dict[str, Any]:
    """词库统计：总数 / 活跃 / 归档 / 活跃分类分布。"""
    conn = mysql_conn(pl)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM prompts")
            total = int(cur.fetchone()[0])
            cur.execute("SELECT COUNT(*) FROM prompts WHERE archived=1")
            archived = int(cur.fetchone()[0])
            cur.execute(
                "SELECT category, COUNT(*) FROM prompts WHERE archived=0 "
                "GROUP BY category ORDER BY COUNT(*) DESC"
            )
            by_category = [
                {"category": str(r[0] or "未分类"), "count": int(r[1])} for r in cur.fetchall()
            ]
        return {"total": total, "active": total - archived, "archived": archived, "by_category": by_category}
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
    s = sub.add_parser("search", help="向量检索提示词（只检索活跃词条）")
    s.add_argument("query", help="需求描述")
    s.add_argument("--k", type=int, default=None, help="返回条数")
    s.add_argument("--json", action="store_true", help="机器可读输出")
    sub.add_parser("init", help="创建数据表")
    sub.add_parser("stats", help="词库统计（总数/活跃/归档/分类）")
    addp = sub.add_parser("add", help="单条入库（自家精品等）")
    addp.add_argument("content", help="提示词正文")
    addp.add_argument("--category", default="自家精品", help="分类（默认：自家精品）")
    addp.add_argument("--tags", default="", help="标签，逗号分隔")
    addp.add_argument("--requirement", default="", help="原始用户需求（用于双向量检索）")
    addp.add_argument("--source", default="", help="来源名称")
    addp.add_argument("--source-url", default="", help="来源链接")
    addp.add_argument("--notes", default="", help="备注")
    bk = sub.add_parser("backup", help="导出全部提示词为 JSONL 备份")
    bk.add_argument("out", nargs="?", default="", help="输出文件（留空=默认备份目录）")
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
                f"导入完成：共 {result['total']} 条，新增 {result['inserted']} 条，"
                f"跳过 {result['skipped']} 条。"
            )
            return 0
        if args.command == "search":
            hits = search(pl, args.query, top_k=args.k, final_k=args.k)
            if getattr(args, "json", False):
                print(json.dumps(hits, ensure_ascii=False, indent=2))
            else:
                for h in hits:
                    print(f"[{h['category']}] {h['content'][:160]}")
                print(f"（共 {len(hits)} 条）")
            return 0
        if args.command == "stats":
            st = stats(pl)
            print(
                f"总数 {st['total']}：活跃 {st['active']}，归档 {st['archived']}。"
            )
            for item in st["by_category"]:
                print(f"  {item['category']}: {item['count']}")
            return 0
        if args.command == "add":
            result = add_prompt(
                pl,
                args.content,
                category=args.category,
                tags=args.tags,
                requirement=args.requirement,
                source=args.source,
                source_url=args.source_url,
                notes=args.notes,
            )
            if result.get("added"):
                print(f"已入库（id={result['id']}，向量 {result['vectors']} 组）。")
            else:
                print("未新增：" + str(result.get("reason") or "未知原因"))
            return 0
        if args.command == "backup":
            result = backup(pl, args.out)
            print(f"备份完成：{result['count']} 条 → {result['path']}")
            return 0
        parser.error(f"未知命令：{args.command}")
        return 2
    except LibError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"错误：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
