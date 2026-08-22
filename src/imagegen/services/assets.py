"""AssetService：Reference Asset 的持久化存储（SQLite 记录 + managed 文件）。

架构边界：Engine 不知道 Asset；HTTP 路由不写 SQL；WebUI 只使用 Asset API。
浏览器上传与服务器本机导入都会转换为 managed asset（复制进 asset 目录，
不登记原始路径），因此用户移动原文件不会导致 Asset Library 失效。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..config import default_asset_dir, default_history_db_path
from ..errors import AssetInUseError, AssetNotFoundError, ValidationError
from ..image_utils import MAX_INIT_BYTES, _guess_mime, probe_image_size_ext
from .db import migrate_db
from .history import utc_now_iso


SUPPORTED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 100

_EXT_BY_MIME = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
}


def normalize_mime(mime: str) -> str:
    """规范化 Content-Type：去参数、image/jpg → image/jpeg。"""
    mime = (mime or "").strip().lower().split(";")[0].strip()
    return "image/jpeg" if mime == "image/jpg" else mime


@dataclass
class AssetRecord:
    """Asset 的轻量数据对象（HTTP 层不得直接拿 sqlite3.Row）。"""

    asset_id: str
    created_at: str
    kind: str
    source: str
    file_path: str
    original_name: Optional[str] = None
    mime_type: Optional[str] = None
    size_bytes: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    sha256: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: Any) -> "AssetRecord":
        try:
            metadata = json.loads(row[11] or "{}")
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        return cls(
            asset_id=row[0],
            created_at=row[1],
            kind=row[2],
            source=row[3],
            file_path=row[4],
            original_name=row[5],
            mime_type=row[6],
            size_bytes=row[7],
            width=row[8],
            height=row[9],
            sha256=row[10],
            metadata=metadata if isinstance(metadata, dict) else {},
        )

    def to_dict(self) -> dict[str, Any]:
        """完整记录（含 file_path / sha256 / metadata；仅供服务层内部与测试）。"""
        return {
            "asset_id": self.asset_id,
            "created_at": self.created_at,
            "kind": self.kind,
            "source": self.source,
            "file_path": self.file_path,
            "original_name": self.original_name,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "width": self.width,
            "height": self.height,
            "sha256": self.sha256,
            "metadata": dict(self.metadata),
        }

    def to_public_dict(self) -> dict[str, Any]:
        """HTTP 对外契约：不暴露 file_path / sha256 / metadata。"""
        return {
            "asset_id": self.asset_id,
            "kind": self.kind,
            "source": self.source,
            "original_name": self.original_name,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "width": self.width,
            "height": self.height,
            "content_url": f"/api/v1/assets/{self.asset_id}/content",
            "created_at": self.created_at,
        }


@dataclass
class AssetLink:
    """generation → asset 引用关系（relation=reference，第一版）。"""

    generation_id: str
    asset_id: str
    relation: str
    role: str
    position: int


class AssetService:
    """Asset 数据与持久化；每次操作独立 connect/execute/commit/close。"""

    def __init__(
        self,
        db_path: str | Path | None = None,
        asset_dir: str | Path | None = None,
    ):
        self.db_path = (
            Path(db_path).expanduser()
            if db_path is not None
            else default_history_db_path()
        )
        self.asset_dir = (
            Path(asset_dir).expanduser()
            if asset_dir is not None
            else default_asset_dir()
        )
        migrate_db(self.db_path)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=5.0)

    def _store(
        self,
        data: bytes,
        source: str,
        kind: str = "reference",
        original_name: str = "",
        mime_type: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> AssetRecord:
        if not data:
            raise ValidationError("图片内容为空。")
        if len(data) > MAX_INIT_BYTES:
            raise ValidationError(
                f"图片过大（{len(data) // 1024} KB），上限 {MAX_INIT_BYTES // (1024 * 1024)} MB。"
            )
        kind = (kind or "reference").strip()
        if not kind:
            raise ValidationError("kind 不能为空。")
        mime = normalize_mime(mime_type or _guess_mime(data, original_name))
        if mime not in SUPPORTED_IMAGE_TYPES:
            raise ValidationError(
                f"不支持的图片格式：{mime or '未知'}。支持 PNG / JPEG / WebP / GIF。"
            )
        asset_id = uuid.uuid4().hex
        created_at = utc_now_iso()
        size = len(data)
        dims = probe_image_size_ext(data, mime)
        sha256 = hashlib.sha256(data).hexdigest()
        ext = _EXT_BY_MIME[mime]
        self.asset_dir.mkdir(parents=True, exist_ok=True)
        file_path = self.asset_dir / f"{asset_id}.{ext}"
        file_path.write_bytes(data)
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO assets "
                "(id, created_at, kind, source, file_path, original_name, mime_type, "
                " size_bytes, width, height, sha256, metadata_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    asset_id,
                    created_at,
                    kind,
                    source,
                    str(file_path),
                    original_name or None,
                    mime,
                    size,
                    dims[0] if dims else None,
                    dims[1] if dims else None,
                    sha256,
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
            conn.commit()
        except Exception:
            try:
                file_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        finally:
            conn.close()
        return AssetRecord(
            asset_id=asset_id,
            created_at=created_at,
            kind=kind,
            source=source,
            file_path=str(file_path),
            original_name=original_name or None,
            mime_type=mime,
            size_bytes=size,
            width=dims[0] if dims else None,
            height=dims[1] if dims else None,
            sha256=sha256,
            metadata=dict(metadata or {}),
        )

    def create_from_upload(
        self,
        data: bytes,
        original_name: str = "",
        kind: str = "reference",
        mime_type: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> AssetRecord:
        """浏览器上传：写入 managed asset 目录并建立 AssetRecord（source=upload）。"""
        return self._store(
            data,
            source="upload",
            kind=kind,
            original_name=original_name,
            mime_type=mime_type,
            metadata=metadata,
        )

    def import_path(
        self,
        path: str | Path,
        kind: str = "reference",
        metadata: Optional[dict[str, Any]] = None,
    ) -> AssetRecord:
        """服务器本机路径导入：读取原文件 → 复制进 managed 目录 → 建立记录。"""
        p = Path(path).expanduser()
        if not p.is_file():
            raise AssetNotFoundError(f"找不到本机图片文件：{p}")
        data = p.read_bytes()
        return self._store(
            data,
            source="local",
            kind=kind,
            original_name=p.name,
            mime_type="",
            metadata=metadata,
        )

    def get(self, asset_id: str) -> Optional[AssetRecord]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM assets WHERE id=?", (asset_id,)
            ).fetchone()
            return AssetRecord.from_row(row) if row else None
        finally:
            conn.close()

    def list(
        self,
        kind: Optional[str] = None,
        query: str = "",
        limit: int = DEFAULT_LIST_LIMIT,
        offset: int = 0,
    ) -> list[AssetRecord]:
        """搜索第一版只做 original_name LIKE；不做 FTS / vector search。"""
        limit = max(1, min(MAX_LIST_LIMIT, int(limit)))
        offset = max(0, int(offset))
        sql = "SELECT * FROM assets"
        clauses: list[str] = []
        params: list[Any] = []
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if query:
            clauses.append("original_name LIKE ?")
            params.append(f"%{query}%")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        conn = self._connect()
        try:
            rows = conn.execute(sql, (*params, limit, offset)).fetchall()
            return [AssetRecord.from_row(row) for row in rows]
        finally:
            conn.close()

    def resolve_path(self, asset_id: str) -> str:
        """asset_id → managed 本地图片路径（ReferenceResolver 使用）。"""
        record = self.get(asset_id)
        if record is None:
            raise AssetNotFoundError(f"unknown asset: {asset_id}")
        file_path = Path(record.file_path)
        if not file_path.is_file():
            raise AssetNotFoundError(f"asset file missing: {asset_id}")
        return str(file_path)

    def delete(self, asset_id: str) -> bool:
        """删除未被 generation_assets 引用的 asset；已引用 → AssetInUseError。"""
        record = self.get(asset_id)
        if record is None:
            return False
        conn = self._connect()
        try:
            used = conn.execute(
                "SELECT 1 FROM generation_assets WHERE asset_id=? LIMIT 1",
                (asset_id,),
            ).fetchone()
            if used is not None:
                raise AssetInUseError(f"asset is referenced by generations: {asset_id}")
            conn.execute("DELETE FROM assets WHERE id=?", (asset_id,))
            conn.commit()
        finally:
            conn.close()
        try:
            Path(record.file_path).unlink(missing_ok=True)
        except OSError:
            pass
        return True

    def attach_to_generation(
        self,
        generation_id: str,
        asset_id: str,
        role: str,
        position: int,
    ) -> AssetLink:
        """记录 generation → asset 引用（relation=reference，第一版）。"""
        record = self.get(asset_id)
        if record is None:
            raise AssetNotFoundError(f"unknown asset: {asset_id}")
        position = int(position)
        if position < 0:
            raise ValidationError("position 不能为负数。")
        role = (role or "auto").strip() or "auto"
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO generation_assets "
                "(generation_id, asset_id, relation, role, position) VALUES (?,?,?,?,?)",
                (generation_id, asset_id, "reference", role, position),
            )
            conn.commit()
        finally:
            conn.close()
        return AssetLink(
            generation_id=generation_id,
            asset_id=asset_id,
            relation="reference",
            role=role,
            position=position,
        )

    def list_for_generation(self, generation_id: str) -> list[AssetLink]:
        """按 position 顺序返回 generation 引用的 assets。"""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT generation_id, asset_id, relation, role, position "
                "FROM generation_assets WHERE generation_id=? "
                "ORDER BY position ASC, asset_id ASC",
                (generation_id,),
            ).fetchall()
            return [
                AssetLink(
                    generation_id=row[0],
                    asset_id=row[1],
                    relation=row[2] or "reference",
                    role=row[3] or "auto",
                    position=row[4],
                )
                for row in rows
            ]
        finally:
            conn.close()
