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
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional

from .config import default_config_path, load_config as load_user_config
from .prompt_case import (
    PROMPT_CASE_EMBEDDING_VERSION,
    PROMPT_CASE_PARSER_VERSION,
    PromptCase,
    QueryAnalysis,
    build_intent_text,
    build_visual_text,
    mode_profile,
    parse_prompt_case,
    parse_query,
    select_diverse_cases,
)


class LibError(Exception):
    """词库功能错误，中文提示。"""


DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "use_in_translator": True,
    "parser": {"enabled": True, "model": ""},
    "intent_top_k": 30,
    "visual_top_k": 20,
    "final_k": 4,
    "categories": [],
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
    # Parser and Query Parser reuse the project's single Translator upstream;
    # keeping this small section here avoids introducing a second endpoint.
    merged["translator"] = cfg.get("translator") if isinstance(cfg.get("translator"), dict) else {}
    return merged


def _embedding_model(pl: dict[str, Any]) -> str:
    """Return the effective embedding model used for new Prompt Case data."""
    embedding = pl.get("embedding") if isinstance(pl.get("embedding"), dict) else {}
    return str(embedding.get("model") or DEFAULTS["embedding"]["model"]).strip()


def _pack_vec(vector: Optional[list[float]]) -> Optional[bytes]:
    """Encode one embedding without ever reusing a legacy blob."""
    if not vector:
        return None
    return struct.pack(f"<{len(vector)}f", *vector)


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
    """建表并以补列方式升级老库，不删除任何旧列或数据。"""
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
                    requirement_embedding LONGBLOB NULL,
                    requirement TEXT NULL,
                    inferred_requirement TEXT NULL,
                    requirement_source VARCHAR(16) NOT NULL DEFAULT 'none',
                    task_type VARCHAR(32) NOT NULL DEFAULT 'text_to_image',
                    facets_json LONGTEXT NULL,
                    transferable_lessons_json LONGTEXT NULL,
                    intent_text LONGTEXT NULL,
                    visual_text LONGTEXT NULL,
                    intent_embedding LONGBLOB NULL,
                    visual_embedding LONGBLOB NULL,
                    parser_version INT NOT NULL DEFAULT 0,
                    embedding_model VARCHAR(255) NOT NULL DEFAULT '',
                    embedding_version INT NOT NULL DEFAULT 0,
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
                ("requirement", "ALTER TABLE prompts ADD COLUMN requirement TEXT NULL"),
                ("inferred_requirement", "ALTER TABLE prompts ADD COLUMN inferred_requirement TEXT NULL"),
                ("requirement_source", "ALTER TABLE prompts ADD COLUMN requirement_source VARCHAR(16) NOT NULL DEFAULT 'none'"),
                ("task_type", "ALTER TABLE prompts ADD COLUMN task_type VARCHAR(32) NOT NULL DEFAULT 'text_to_image'"),
                ("facets_json", "ALTER TABLE prompts ADD COLUMN facets_json LONGTEXT NULL"),
                ("transferable_lessons_json", "ALTER TABLE prompts ADD COLUMN transferable_lessons_json LONGTEXT NULL"),
                ("intent_text", "ALTER TABLE prompts ADD COLUMN intent_text LONGTEXT NULL"),
                ("visual_text", "ALTER TABLE prompts ADD COLUMN visual_text LONGTEXT NULL"),
                ("intent_embedding", "ALTER TABLE prompts ADD COLUMN intent_embedding LONGBLOB NULL"),
                ("visual_embedding", "ALTER TABLE prompts ADD COLUMN visual_embedding LONGBLOB NULL"),
                ("parser_version", "ALTER TABLE prompts ADD COLUMN parser_version INT NOT NULL DEFAULT 0"),
                ("embedding_model", "ALTER TABLE prompts ADD COLUMN embedding_model VARCHAR(255) NOT NULL DEFAULT ''"),
                ("embedding_version", "ALTER TABLE prompts ADD COLUMN embedding_version INT NOT NULL DEFAULT 0"),
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
        "User-Agent": "imagegen/prompt-lib",
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


def rerank_scored_docs(
    pl: dict[str, Any], query: str, documents: list[str], top_n: int
) -> list[tuple[int, float]]:
    """Rerank documents while preserving relevance scores for MMR selection."""
    r = pl.get("rerank") or {}
    if not r.get("enabled"):
        return [(index, 0.0) for index in range(len(documents))]
    api_key = str(r.get("api_key") or "").strip()
    if not api_key or not documents:
        return [(index, 0.0) for index in range(len(documents))]
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
    return [(idx, score) for idx, score in ordered if 0 <= idx < len(documents)]


def rerank_docs(pl: dict[str, Any], query: str, documents: list[str], top_n: int) -> list[int]:
    """Compatibility wrapper returning only indices."""
    return [index for index, _score in rerank_scored_docs(pl, query, documents, top_n)]


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


def _pick_intent_vec(
    intent_blob: Any,
    requirement_blob: Any,
    embedding_blob: Any,
) -> list[float]:
    """Pick the best available intent vector during a rolling migration.

    The two legacy blobs are intentionally only fallbacks.  In particular,
    ``embedding`` is never treated as a visual vector.
    """
    for blob in (intent_blob, requirement_blob, embedding_blob):
        vector = _decode_vec(blob)
        if vector:
            return vector
    return []


def _pick_vec(embedding_blob: Any, requirement_blob: Any) -> list[float]:
    """Backward-compatible wrapper for callers that only know legacy columns."""
    return _pick_intent_vec(None, requirement_blob, embedding_blob)


def _decode_vec(blob: Any) -> list[float]:
    if not blob:
        return []
    try:
        raw = bytes(blob)
        usable = len(raw) - (len(raw) % 4)
        if usable <= 0:
            return []
        return list(struct.unpack(f"<{usable // 4}f", raw[:usable]))
    except (TypeError, ValueError, struct.error):
        return []


def _decode_json(value: Any, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value) if isinstance(value, (str, bytes, bytearray)) else value
    except (TypeError, json.JSONDecodeError):
        return default


def _cosine_scores(query_vec: list[float], vectors: list[list[float]]) -> list[float]:
    if not query_vec:
        return [0.0 for _ in vectors]
    try:
        import numpy as np

        q = np.asarray(query_vec, dtype=np.float32)
        if q.size == 0:
            return [0.0 for _ in vectors]
        q = q / max(float(np.linalg.norm(q)), 1e-12)
        result = []
        for vec in vectors:
            if not vec or len(vec) != len(query_vec):
                result.append(0.0)
                continue
            v = np.asarray(vec, dtype=np.float32)
            result.append(float(np.dot(q, v / max(float(np.linalg.norm(v)), 1e-12))))
        return result
    except ImportError:
        def cosine(vec: list[float]) -> float:
            if not vec or len(vec) != len(query_vec):
                return 0.0
            dot = sum(float(x) * float(y) for x, y in zip(query_vec, vec))
            qn = sum(float(x) * float(x) for x in query_vec) ** 0.5
            vn = sum(float(x) * float(x) for x in vec) ** 0.5
            return dot / (qn * vn) if qn and vn else 0.0
        return [cosine(vec) for vec in vectors]


def _case_from_row(row: tuple[Any, ...]) -> PromptCase:
    return PromptCase.from_dict(
        {
            "id": int(row[0]),
            "content": row[1],
            "facets": _decode_json(row[12], {}),
            "requirement": row[8],
            "inferred_requirement": row[9],
            "requirement_source": row[10],
            "task_type": row[11],
            "intent_text": row[14],
            "visual_text": row[15],
            "transferable_lessons": _decode_json(row[13], []),
            "parser_version": row[18],
        }
    )


def search_prompt_cases(
    pl: dict[str, Any],
    query: str,
    *,
    query_analysis: Optional[QueryAnalysis] = None,
    prompt_mode: str = "optimized",
    top_k: Optional[int] = None,
    intent_top_k: Optional[int] = None,
    visual_top_k: Optional[int] = None,
    final_k: Optional[int] = None,
    categories: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """Dual-path Intent/Visual retrieval followed by structured rerank + MMR."""
    profile = mode_profile(prompt_mode)
    configured_intent = pl.get("intent_top_k")
    configured_visual = pl.get("visual_top_k")
    configured_final = pl.get("final_k")
    intent_top_k = int(
        intent_top_k
        or top_k
        or (configured_intent if configured_intent not in (None, "", 30) else profile["intent_top_k"])
    )
    visual_top_k = int(
        visual_top_k
        or (configured_visual if configured_visual not in (None, "", 20) else profile["visual_top_k"])
    )
    final_k = int(
        final_k
        or (configured_final if configured_final not in (None, "", 4) else profile["final_k"])
    )
    if not str(query or "").strip():
        raise LibError("检索关键词不能为空。")
    if query_analysis is None:
        query_analysis = parse_query(query, cfg={"prompt_library": pl, "translator": pl.get("translator", {})})
    cats = [str(c).strip() for c in (categories or pl.get("categories") or []) if str(c).strip()]
    intent_text = build_intent_text(query_analysis) or str(query).strip()
    visual_text = build_visual_text(query_analysis)
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
            cur.execute(
                "SELECT id, content, category, tags, source, source_url, embedding, requirement_embedding,"
                " requirement, inferred_requirement, requirement_source, task_type, facets_json,"
                " transferable_lessons_json, intent_text, visual_text, intent_embedding, visual_embedding, parser_version"
                " FROM prompts" + where,
                params,
            )
            rows = cur.fetchall()
            query_vectors = embed_texts(
                pl,
                [intent_text] + ([visual_text] if visual_text else []),
                input_type="query",
            )
            intent_query_vec = query_vectors[0] if query_vectors else []
            visual_query_vec = query_vectors[1] if visual_text and len(query_vectors) > 1 else []
            cases: list[PromptCase] = []
            intent_vectors: list[list[float]] = []
            visual_vectors: list[list[float]] = []
            for row in rows:
                case = _case_from_row(row)
                cases.append(case)
                intent_vectors.append(
                    _pick_intent_vec(row[16], row[7], row[6])
                )
                visual_vectors.append(_decode_vec(row[17]))
            # Old rows have no new vectors.  Build temporary vectors from the
            # structured text, while still preserving the original content.
            missing_intent = [i for i, vec in enumerate(intent_vectors) if not vec]
            if missing_intent:
                temp = embed_texts(
                    pl,
                    [build_intent_text(case) or case.content for i, case in enumerate(cases) if i in missing_intent],
                    input_type="document",
                )
                for i, vec in zip(missing_intent, temp):
                    intent_vectors[i] = vec
            intent_scores = _cosine_scores(intent_query_vec, intent_vectors)
            visual_scores = _cosine_scores(visual_query_vec, visual_vectors) if visual_text else [0.0] * len(rows)
            intent_order = sorted(range(len(rows)), key=lambda i: intent_scores[i], reverse=True)[:intent_top_k]
            visual_order = (
                sorted(
                    (i for i in range(len(rows)) if visual_vectors[i]),
                    key=lambda i: visual_scores[i],
                    reverse=True,
                )[:visual_top_k]
                if visual_text else []
            )
            candidate_indices = list(dict.fromkeys(intent_order + visual_order))
            if not candidate_indices:
                return []
            documents = []
            for i in candidate_indices:
                case = cases[i]
                documents.append(
                    "\n".join(
                        part for part in (
                            case.requirement or case.inferred_requirement,
                            case.intent_text,
                            "可迁移技巧：" + "；".join(case.transferable_lessons[:5]),
                        ) if part
                    )
                )
            rerank_query = "\n".join(
                part for part in (str(query).strip(), f"模式：{prompt_mode}", intent_text, visual_text) if part
            )
            ranked: list[tuple[int, float]] = []
            try:
                reranked = rerank_scored_docs(pl, rerank_query, documents, len(documents))
                if reranked and any(score != 0.0 for _idx, score in reranked):
                    ranked = [(candidate_indices[idx], score) for idx, score in reranked]
            except LibError:
                ranked = []
            if not ranked:
                ranked = sorted(
                    (
                        (i, 0.65 * intent_scores[i] + (0.35 * visual_scores[i] if visual_text else 0.0))
                        for i in candidate_indices
                    ),
                    key=lambda pair: pair[1],
                    reverse=True,
                )
            candidate_dicts: list[dict[str, Any]] = []
            score_by_index = dict(ranked)
            for i in candidate_indices:
                case = cases[i]
                candidate_dicts.append(
                    {
                        "id": int(row_id := rows[i][0]),
                        "requirement": case.requirement,
                        "inferred_requirement": case.inferred_requirement,
                        "requirement_source": case.requirement_source,
                        "content": case.content,
                        "category": str(rows[i][2] or ""),
                        "tags": str(rows[i][3] or ""),
                        "source": str(rows[i][4] or ""),
                        "facets": case.facets.to_dict(),
                        "transferable_lessons": list(case.transferable_lessons),
                        "intent_score": float(intent_scores[i]),
                        "visual_score": float(visual_scores[i]),
                        "rerank_score": float(score_by_index.get(i, 0.0)),
                        "_visual_vector": visual_vectors[i],
                    }
                )
            return select_diverse_cases(candidate_dicts, final_k=final_k, prompt_mode=prompt_mode)
    finally:
        conn.close()


def search(
    pl: dict[str, Any],
    query: str,
    top_k: Optional[int] = None,
    final_k: Optional[int] = None,
    categories: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """Compatibility wrapper for callers that have not adopted case search."""
    return search_prompt_cases(
        pl,
        query,
        top_k=top_k,
        intent_top_k=top_k,
        final_k=final_k,
        categories=categories,
    )


def _sha1(text: str) -> str:
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()


def add_prompt(
    pl: dict[str, Any],
    content: str,
    *,
    category: str = "未分类",
    tags: str = "",
    requirement: str = "",
    source: str = "",
    source_url: str = "",
    notes: str = "",
    task_type: str = "text_to_image",
    lang: str = "zh",
) -> dict[str, Any]:
    """Parse, structure and store one Prompt Case without changing content."""
    content = str(content or "").strip()
    if len(content) < 8:
        raise LibError("提示词内容太短（至少 8 个字）。")
    check_conn = mysql_conn(pl)
    try:
        with check_conn.cursor() as cur:
            cur.execute("SELECT id FROM prompts WHERE content_hash=%s", (_sha1(content),))
            if cur.fetchone():
                return {"added": False, "reason": "已存在相同内容的提示词"}
    finally:
        check_conn.close()
    req = str(requirement or "").strip()
    parser_cfg = {"prompt_library": pl, "translator": pl.get("translator", {})}
    case = parse_prompt_case(req, content, cfg=parser_cfg)
    case.task_type = str(task_type or case.task_type or "text_to_image")[:32]
    intent_text = case.intent_text or req or case.inferred_requirement or content
    visual_text = case.visual_text
    new_texts = [intent_text] + ([visual_text] if visual_text else [])
    vectors = embed_texts(pl, new_texts, input_type="document")
    intent_vec = vectors[0] if vectors else []
    visual_offset = 1 if visual_text else None
    visual_vec = vectors[visual_offset] if visual_offset is not None and len(vectors) > visual_offset else None
    if not intent_vec or (visual_text and not visual_vec):
        raise LibError("Embedding 返回的 Prompt Case 向量数量不足。")
    intent_blob = _pack_vec(intent_vec)
    visual_blob = _pack_vec(visual_vec)
    # LEGACY COMPATIBILITY ONLY: new writes do not require the old vectors.
    # Existing rows keep their legacy blobs for fallback and recovery.
    legacy_content_blob = None
    legacy_requirement_blob = None
    conn = mysql_conn(pl)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM prompts WHERE content_hash=%s", (_sha1(content),))
            if cur.fetchone():
                return {"added": False, "reason": "已存在相同内容的提示词"}
            cur.execute(
                """
                INSERT INTO prompts
                    (content, content_hash, lang, category, tags, source, source_url, embedding,
                     requirement_embedding, requirement, inferred_requirement, requirement_source,
                     task_type, facets_json, transferable_lessons_json, intent_text, visual_text,
                     intent_embedding, visual_embedding, parser_version, embedding_model,
                     embedding_version, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    content,
                    _sha1(content),
                    str(lang or "zh").strip()[:16],
                    str(category or "").strip()[:64] or "未分类",
                    str(tags or "").strip()[:512],
                    str(source or "").strip()[:255],
                    str(source_url or "").strip()[:512],
                    legacy_content_blob,
                    legacy_requirement_blob,
                    case.requirement,
                    case.inferred_requirement,
                    case.requirement_source,
                    case.task_type,
                    json.dumps(case.facets.to_dict(), ensure_ascii=False),
                    json.dumps(case.transferable_lessons, ensure_ascii=False),
                    intent_text,
                    visual_text,
                    intent_blob,
                    visual_blob,
                    case.parser_version,
                    _embedding_model(pl),
                    PROMPT_CASE_EMBEDDING_VERSION,
                    str(notes or "").strip()[:512],
                ),
            )
            pid = int(cur.lastrowid)
        return {
            "added": True,
            "id": pid,
            "vectors": 2 if visual_vec is not None else 1,
            "parser_version": case.parser_version,
        }
    finally:
        conn.close()


def backup(pl: dict[str, Any], out_path: str = "") -> dict[str, Any]:
    """把全部提示词导出为 JSONL 备份。"""
    conn = mysql_conn(pl)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT content, lang, category, tags, source, source_url, notes, requirement,"
                " inferred_requirement, requirement_source, task_type, facets_json,"
                " transferable_lessons_json, intent_text, visual_text, parser_version,"
                " embedding_model, embedding_version"
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
                        "requirement": r[7],
                        "inferred_requirement": r[8],
                        "requirement_source": r[9],
                        "task_type": r[10],
                        "facets": _decode_json(r[11], {}),
                        "transferable_lessons": _decode_json(r[12], []),
                        "intent_text": r[13],
                        "visual_text": r[14],
                        "parser_version": r[15],
                        "embedding_model": r[16],
                        "embedding_version": r[17],
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
    default_lang: str = "zh",
    default_category: str = "",
    default_tags: str = "",
    default_source: str = "",
    default_url: str = "",
) -> dict[str, int]:
    """Import old or structured records through the Prompt Case pipeline."""
    if not items:
        return {"total": 0, "inserted": 0, "skipped": 0, "failed": 0}
    cleaned: list[dict[str, Any]] = []
    skipped = 0
    for it in items:
        if not isinstance(it, dict):
            skipped += 1
            continue
        content = str(it.get("content") or it.get("prompt") or it.get("text") or "").strip()
        if len(content) < 8:
            skipped += 1
            continue
        cleaned.append(
            {
                "content": content,
                "lang": str(it.get("lang") or default_lang or "zh").strip()[:16],
                "category": str(it.get("category") or default_category or "未分类").strip()[:64],
                "tags": str(it.get("tags") or default_tags).strip()[:512],
                "source": str(it.get("source") or default_source).strip()[:255],
                "source_url": str(it.get("source_url") or default_url).strip()[:512],
                "requirement": str(it.get("requirement") or "").strip(),
                "task_type": str(it.get("task_type") or "text_to_image").strip()[:32],
                "notes": str(it.get("notes") or "").strip()[:512],
            }
        )
    if not cleaned:
        return {"total": len(items), "inserted": 0, "skipped": len(items), "failed": 0}
    conn = mysql_conn(pl)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT content_hash FROM prompts")
            existing = {str(row[0]) for row in cur.fetchall()}
    finally:
        conn.close()
    inserted = 0
    failed = 0
    seen_local: set[str] = set()
    for it in cleaned:
        digest = _sha1(it["content"])
        if digest in existing or digest in seen_local:
            skipped += 1
            continue
        seen_local.add(digest)
        try:
            result = add_prompt(
                pl,
                it["content"],
                category=it["category"],
                tags=it["tags"],
                requirement=it["requirement"],
                source=it["source"],
                source_url=it["source_url"],
                notes=it["notes"],
                task_type=it["task_type"],
                lang=it["lang"],
            )
            if result.get("added"):
                inserted += 1
            else:
                skipped += 1
        except Exception:
            failed += 1
    return {"total": len(items), "inserted": inserted, "skipped": skipped, "failed": failed}


def rebuild_cases(
    pl: dict[str, Any],
    *,
    force: bool = False,
    limit: Optional[int] = None,
    workers: int = 1,
) -> dict[str, int]:
    """Rebuild Prompt Case data without touching legacy source columns.

    A row is only updated after parsing and all required new embeddings have
    completed successfully.  Workers use MySQL connection-level advisory
    locks and re-read the row after claiming it, so concurrent commands cannot
    process the same pending row at the same time.  One bad parser response or
    unavailable embedding request still cannot stop the other workers.
    """
    current_model = _embedding_model(pl)
    if limit is not None:
        try:
            limit_value = int(limit)
        except (TypeError, ValueError) as exc:
            raise LibError("--limit 必须是非负整数。") from exc
        if limit_value < 0:
            raise LibError("--limit 必须是非负整数。")
    else:
        limit_value = None
    try:
        workers_value = int(workers)
    except (TypeError, ValueError) as exc:
        raise LibError("--workers 必须是正整数。") from exc
    if workers_value < 1 or workers_value > 32:
        raise LibError("--workers 必须是 1 到 32 之间的整数。")
    conn = mysql_conn(pl)
    try:
        with conn.cursor() as cur:
            # Archived rows are intentionally excluded from migration.  They
            # are not part of retrieval and should never consume parser or
            # embedding API capacity, even when --force is supplied.
            where = " WHERE archived=0"
            params: list[Any] = []
            if not force:
                where += (
                    " AND (COALESCE(parser_version, 0) < %s"
                    " OR intent_embedding IS NULL"
                    " OR COALESCE(embedding_version, 0) < %s"
                    " OR COALESCE(embedding_model, '') <> %s"
                    " OR (NULLIF(TRIM(visual_text), '') IS NOT NULL"
                    "     AND visual_embedding IS NULL))"
                )
                params = [
                    PROMPT_CASE_PARSER_VERSION,
                    PROMPT_CASE_EMBEDDING_VERSION,
                    current_model,
                ]
            sql = (
                "SELECT id, content, requirement, task_type FROM prompts"
                + where + " ORDER BY id"
            )
            if limit_value is not None:
                sql += " LIMIT %s"
                params.append(limit_value)
            cur.execute(sql, params)
            rows = list(cur.fetchall())
    finally:
        conn.close()
    result = {"total": len(rows), "success": 0, "failed": 0, "skipped": 0}
    def record_status(status: str) -> None:
        if status == "success":
            result["success"] += 1
        elif status == "skipped":
            result["skipped"] += 1
        else:
            result["failed"] += 1

    if workers_value == 1:
        for row in rows:
            record_status(_rebuild_case_row(pl, row, current_model, force=force))
    else:
        with ThreadPoolExecutor(
            max_workers=workers_value,
            thread_name_prefix="prompt-case-migration",
        ) as executor:
            futures = [
                executor.submit(_rebuild_case_row, pl, row, current_model, force)
                for row in rows
            ]
            for future in as_completed(futures):
                try:
                    record_status(future.result())
                except Exception:
                    # A worker-level failure must not prevent other workers
                    # from completing their already claimed rows.
                    record_status("failed")
    return result


def _case_row_needs_rebuild(row: tuple[Any, ...], current_model: str) -> bool:
    """Check a freshly read row against the current Prompt Case versions."""
    parser_version = int(row[4] or 0)
    embedding_version = int(row[6] or 0)
    embedding_model = str(row[7] or "")
    visual_text = str(row[8] or "").strip()
    return bool(
        parser_version < PROMPT_CASE_PARSER_VERSION
        or row[5] is None
        or embedding_version < PROMPT_CASE_EMBEDDING_VERSION
        or embedding_model != current_model
        or (visual_text and row[9] is None)
    )


def _rebuild_case_row(
    pl: dict[str, Any],
    row: tuple[Any, ...],
    current_model: str,
    force: bool,
) -> str:
    """Claim, rebuild and release one row using a MySQL advisory lock."""
    prompt_id = int(row[0])
    mysql_cfg = pl.get("mysql") if isinstance(pl.get("mysql"), dict) else {}
    db_name = str(mysql_cfg.get("db") or "prompt_library")
    db_scope = hashlib.sha1(db_name.encode("utf-8")).hexdigest()[:12]
    lock_name = f"imagegen:prompt_case:{db_scope}:{prompt_id}"
    conn = None
    lock_held = False
    try:
        conn = mysql_conn(pl)
        with conn.cursor() as cur:
            cur.execute("SELECT GET_LOCK(%s, 0)", (lock_name,))
            lock_result = cur.fetchone()
        if not lock_result or int(lock_result[0] or 0) != 1:
            return "skipped"
        lock_held = True

        # The initial candidate list can be stale when another migration
        # process finishes the row before this worker obtains the lock.
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, content, requirement, task_type, parser_version,"
                " intent_embedding, embedding_version, embedding_model,"
                " visual_text, visual_embedding FROM prompts WHERE id=%s",
                (prompt_id,),
            )
            current_row = cur.fetchone()
        if not current_row or (not force and not _case_row_needs_rebuild(current_row, current_model)):
            return "skipped"

        # Keep the database's original requirement untouched.  The parser
        # receives it as context, while the UPDATE below only writes derived
        # Prompt Case fields and the new vectors.
        original_requirement = str(current_row[2] or "")
        case = parse_prompt_case(
            original_requirement,
            str(current_row[1] or ""),
            cfg={"prompt_library": pl, "translator": pl.get("translator", {})},
            strict=True,
        )
        if int(case.parser_version or 0) != PROMPT_CASE_PARSER_VERSION:
            raise LibError("Prompt Case Parser 未生成当前版本的结构化结果。")
        intent_text = case.intent_text or case.requirement or case.inferred_requirement or case.content
        if not intent_text:
            raise LibError("无法构造非空 intent_text。")
        visual_text = case.visual_text or ""
        texts = [intent_text] + ([visual_text] if visual_text else [])
        vectors = embed_texts(pl, texts, input_type="document")
        if not vectors or not vectors[0]:
            raise LibError("Embedding 未返回 intent 向量。")
        if visual_text and (len(vectors) < 2 or not vectors[1]):
            raise LibError("Embedding 未返回 visual 向量。")
        intent_blob = _pack_vec(vectors[0])
        visual_blob = _pack_vec(vectors[1] if visual_text else None)
        inferred_requirement = "" if original_requirement.strip() else case.inferred_requirement
        requirement_source = (
            "user"
            if original_requirement.strip()
            else ("inferred" if inferred_requirement else "none")
        )
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE prompts SET inferred_requirement=%s,
                requirement_source=%s, task_type=%s, facets_json=%s,
                transferable_lessons_json=%s, intent_text=%s, visual_text=%s,
                intent_embedding=%s, visual_embedding=%s, parser_version=%s,
                embedding_model=%s, embedding_version=%s WHERE id=%s
                """,
                (
                    inferred_requirement,
                    requirement_source,
                    str(current_row[3] or case.task_type or "text_to_image")[:32],
                    json.dumps(case.facets.to_dict(), ensure_ascii=False),
                    json.dumps(case.transferable_lessons, ensure_ascii=False),
                    intent_text,
                    visual_text,
                    intent_blob,
                    visual_blob,
                    PROMPT_CASE_PARSER_VERSION,
                    current_model,
                    PROMPT_CASE_EMBEDDING_VERSION,
                    prompt_id,
                ),
            )
        return "success"
    except Exception:
        return "failed"
    finally:
        if conn is not None:
            if lock_held:
                try:
                    with conn.cursor() as cur:
                        cur.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
                except Exception:
                    # Closing the connection also releases the advisory lock.
                    pass
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
    """词库统计，包括 Prompt Case 迁移就绪度。"""
    current_model = _embedding_model(pl)
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
            cur.execute(
                "SELECT COUNT(*) FROM prompts WHERE intent_embedding IS NOT NULL"
                " AND parser_version=%s AND embedding_version=%s AND embedding_model=%s",
                (
                    PROMPT_CASE_PARSER_VERSION,
                    PROMPT_CASE_EMBEDDING_VERSION,
                    current_model,
                ),
            )
            case_ready = int(cur.fetchone()[0])
        return {
            "total": total,
            "active": total - archived,
            "archived": archived,
            "by_category": by_category,
            "case_ready": case_ready,
            "case_pending": max(0, total - case_ready),
            "parser_version_current": PROMPT_CASE_PARSER_VERSION,
            "embedding_model_current": current_model,
            "embedding_version_current": PROMPT_CASE_EMBEDDING_VERSION,
        }
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
    sub.add_parser("stats", help="词库统计（总数/活跃/归档/分类/迁移就绪度）")
    addp = sub.add_parser("add", help="单条入库为 Prompt Case")
    addp.add_argument("content", help="提示词正文")
    addp.add_argument("--category", default="未分类", help="管理分类（默认：未分类）")
    addp.add_argument("--tags", default="", help="标签，逗号分隔")
    addp.add_argument("--requirement", default="", help="原始用户需求（用于双向量检索）")
    addp.add_argument("--source", default="", help="来源名称")
    addp.add_argument("--source-url", default="", help="来源链接")
    addp.add_argument("--notes", default="", help="备注")
    bk = sub.add_parser("backup", help="导出全部提示词为 JSONL 备份")
    bk.add_argument("out", nargs="?", default="", help="输出文件（留空=默认备份目录）")
    rebuild = sub.add_parser("rebuild-cases", help="重建旧提示词的 Prompt Case 字段与双向量")
    rebuild.add_argument("--force", action="store_true", help="重新解析并重建所有记录的 Prompt Case 数据")
    rebuild.add_argument("--limit", type=int, default=None, help="最多处理需要迁移的记录数")
    rebuild.add_argument(
        "--workers",
        type=int,
        default=1,
        help="并发 worker 数（默认 1，建议根据接口限流设置为 2-8）",
    )
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
                default_lang=args.lang,
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
            print(f"Prompt Case：就绪 {st['case_ready']}，待迁移 {st['case_pending']}。")
            for item in st["by_category"]:
                print(f"  {item['category']}: {item['count']}")
            return 0
        if args.command == "rebuild-cases":
            # The rebuild command is intentionally self-contained: an old
            # database receives new nullable columns before rows are read.
            init_db(pl)
            result = rebuild_cases(
                pl,
                force=bool(args.force),
                limit=args.limit,
                workers=args.workers,
            )
            print(
                f"重建完成：总计 {result['total']} 条，成功 {result['success']} 条，"
                f"失败 {result['failed']} 条，跳过 {result['skipped']} 条。"
            )
            return 0 if result["failed"] == 0 else 1
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
