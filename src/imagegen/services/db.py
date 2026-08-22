"""ImageGen 2 SQLite 基线：generations + assets + generation_assets。

DB_SCHEMA_VERSION = 1 表示 ImageGen 2.x 数据库 schema lineage 的第一个版本，
它不是 ImageGen 1.x / HTTP API v1 / Core API v1。

初始化规则（initialize_db）：
1. DB 文件不存在 → 创建当前 schema。
2. DB 已是当前 ImageGen 2 schema → 正常打开（幂等）。
3. 已存在的 DB 与当前 schema 不兼容 → 明确抛 IncompatibleDatabaseError，
   绝不自动 DROP / 删除 / 清空 / 重建用户数据。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ..errors import IncompatibleDatabaseError


SCHEMA_GENERATIONS = """
CREATE TABLE IF NOT EXISTS generations (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    output_path TEXT NOT NULL,
    image_model_used TEXT,
    prompt TEXT NOT NULL,
    prompt_used TEXT,
    seed INTEGER,
    requested_size TEXT,
    actual_size TEXT,
    warnings_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    request_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_generations_created_at ON generations(created_at DESC);
"""

SCHEMA_ASSETS = """
CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    source TEXT NOT NULL,
    file_path TEXT NOT NULL,
    original_name TEXT,
    mime_type TEXT,
    size_bytes INTEGER,
    width INTEGER,
    height INTEGER,
    sha256 TEXT,
    metadata_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assets_created_at ON assets(created_at DESC);
CREATE TABLE IF NOT EXISTS generation_assets (
    generation_id TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    role TEXT,
    position INTEGER NOT NULL,
    PRIMARY KEY (generation_id, asset_id, position)
);
CREATE INDEX IF NOT EXISTS idx_generation_assets_asset ON generation_assets(asset_id);
CREATE INDEX IF NOT EXISTS idx_generation_assets_generation ON generation_assets(generation_id);
"""

DB_SCHEMA_VERSION = 1

_SCHEMA_TABLES = {"generations", "assets", "generation_assets"}


def initialize_db(db_path: str | Path) -> None:
    """初始化 ImageGen 2 SQLite 数据库；重复调用幂等。"""
    path = Path(db_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    conn = sqlite3.connect(path, timeout=5.0)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version == DB_SCHEMA_VERSION:
            return
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if existed and (version != 0 or bool(tables & _SCHEMA_TABLES)):
            raise IncompatibleDatabaseError(
                f"数据库 {path} 已存在且 schema 与 ImageGen 2 "
                f"(user_version={DB_SCHEMA_VERSION}) 不兼容（当前 user_version="
                f"{version}）。为保护已有数据，程序不会自动删除或重建该数据库；"
                "请人工处理该文件后再启动。"
            )
        conn.executescript(SCHEMA_GENERATIONS)
        conn.executescript(SCHEMA_ASSETS)
        conn.execute(f"PRAGMA user_version = {DB_SCHEMA_VERSION}")
        conn.commit()
    finally:
        conn.close()
