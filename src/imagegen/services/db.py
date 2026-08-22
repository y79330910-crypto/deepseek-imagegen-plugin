"""ImageGen SQLite schema v2：generations + assets + generation_assets。

单一迁移入口（HistoryService / AssetService 共用）：PRAGMA user_version 驱动，
无 ORM、无 migration framework。版本 0 → 2（全新创建）；1 → 2（Phase 5A 升级）；
2 保持不变。重复调用幂等。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA_GENERATIONS = """
CREATE TABLE IF NOT EXISTS generations (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    output_path TEXT NOT NULL,
    backend TEXT,
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

CURRENT_SCHEMA_VERSION = 2


def migrate_db(db_path: str | Path) -> None:
    """创建 / 升级到 schema v2；重复调用幂等。"""
    path = Path(db_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5.0)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version >= CURRENT_SCHEMA_VERSION:
            return
        if version == 0:
            conn.executescript(SCHEMA_GENERATIONS)
        conn.executescript(SCHEMA_ASSETS)
        conn.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")
        conn.commit()
    finally:
        conn.close()
