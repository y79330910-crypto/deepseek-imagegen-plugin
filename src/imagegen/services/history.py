"""Generation 持久化层：sqlite3 存储 + HistoryService（无 ORM / 连接池 / 异步）。

架构边界：CLI / HTTP / WebUI 只依赖 HistoryService；Engine 不知道 SQLite；
HTTP 层不写 SQL；WebUI 不知道 History 实现。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..config import default_history_db_path
from ..models import GenerateRequest, GenerateResult
from .db import migrate_db


MAX_LIMIT = 100
DEFAULT_LIMIT = 50


def utc_now_iso() -> str:
    """统一 UTC ISO 8601，例：2026-08-22T03:31:46.123Z。"""
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _loads(raw: Optional[str], default: Any) -> Any:
    if not raw:
        return list(default) if isinstance(default, list) else dict(default)
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return list(default) if isinstance(default, list) else dict(default)


@dataclass
class HistoryRecord:
    """Generation 历史记录（SQLite Row 的轻量对象，HTTP 层不得直接拿 sqlite3.Row）。"""

    id: str
    created_at: str
    output_path: str
    backend: str = ""
    image_model_used: str = ""
    prompt: str = ""
    prompt_used: str = ""
    seed: Optional[int] = None
    requested_size: str = ""
    actual_size: str = ""
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    request: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: Any) -> "HistoryRecord":
        return cls(
            id=row[0],
            created_at=row[1],
            output_path=row[2],
            backend=row[3] or "",
            image_model_used=row[4] or "",
            prompt=row[5] or "",
            prompt_used=row[6] or "",
            seed=row[7],
            requested_size=row[8] or "",
            actual_size=row[9] or "",
            warnings=_loads(row[10], []),
            metadata=_loads(row[11], {}),
            request=_loads(row[12], {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "output_path": self.output_path,
            "backend": self.backend,
            "image_model_used": self.image_model_used,
            "prompt": self.prompt,
            "prompt_used": self.prompt_used,
            "seed": self.seed,
            "requested_size": self.requested_size,
            "actual_size": self.actual_size,
            "warnings": list(self.warnings),
            "metadata": dict(self.metadata),
            "request": dict(self.request),
        }


class HistoryService:
    """生成历史持久化：每次操作独立 connect/execute/commit/close。"""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = (
            Path(db_path).expanduser() if db_path is not None else default_history_db_path()
        )
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=5.0)

    def _migrate(self) -> None:
        migrate_db(self.db_path)

    def record(self, request: GenerateRequest, result: GenerateResult) -> HistoryRecord:
        """把一个完成的 generation 落库（调用者不负责拆字段）。"""
        rec = HistoryRecord(
            id=result.generation_id,
            created_at=utc_now_iso(),
            output_path=result.path,
            backend=result.backend,
            image_model_used=result.image_model_used,
            prompt=(request.prompt or ""),
            prompt_used=result.prompt_used,
            seed=result.seed,
            requested_size=result.requested_size,
            actual_size=result.actual_size,
            warnings=list(result.warnings),
            metadata={},
            request=request.to_dict(),
        )
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO generations "
                "(id, created_at, output_path, backend, image_model_used, prompt, prompt_used, "
                " seed, requested_size, actual_size, warnings_json, metadata_json, request_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    rec.id,
                    rec.created_at,
                    rec.output_path,
                    rec.backend,
                    rec.image_model_used,
                    rec.prompt,
                    rec.prompt_used,
                    rec.seed,
                    rec.requested_size,
                    rec.actual_size,
                    json.dumps(rec.warnings, ensure_ascii=False),
                    json.dumps(rec.metadata, ensure_ascii=False),
                    json.dumps(rec.request, ensure_ascii=False),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return rec

    def get(self, generation_id: str) -> Optional[HistoryRecord]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM generations WHERE id=?", (generation_id,)
            ).fetchone()
            return HistoryRecord.from_row(row) if row else None
        finally:
            conn.close()

    def list(
        self,
        query: str = "",
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> list[HistoryRecord]:
        limit = max(1, min(MAX_LIMIT, int(limit)))
        offset = max(0, int(offset))
        conn = self._connect()
        try:
            if query:
                like = f"%{query}%"
                rows = conn.execute(
                    "SELECT * FROM generations WHERE prompt LIKE ? OR prompt_used LIKE ? "
                    "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (like, like, limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM generations ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
            return [HistoryRecord.from_row(row) for row in rows]
        finally:
            conn.close()

    def delete(self, generation_id: str) -> bool:
        conn = self._connect()
        try:
            cur = conn.execute("DELETE FROM generations WHERE id=?", (generation_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()
