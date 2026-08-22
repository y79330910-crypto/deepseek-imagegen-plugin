"""ImageGen 2 SQLite：schema v2 + 最小 migration runner。

DB_SCHEMA_VERSION = 2 表示 ImageGen 2.x 数据库 schema lineage 的第二个版本，
它不是 ImageGen 1.x / HTTP API v1 / Core API v1。

schema v1 → v2 只重建 generation_assets（加入真正的外键约束），
不重写 generations / assets，不删除任何 generation / asset 数据。

初始化 / 迁移规则（initialize_db）：
1. DB 不存在，或 user_version == 0 且没有 ImageGen 业务表 → 创建 schema v2。
2. user_version == 1 → 事务化迁移 1 → 2（all-or-nothing）。
3. user_version == 2 → 正常打开（幂等）。
4. user_version > 2 或无法识别的旧数据库 → IncompatibleDatabaseError，
   绝不自动 DROP / 删除 / 清空 / 重建用户数据。

迁移结构面向未来 2 → 3、3 → 4 扩展；本次只实现 1 → 2。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Callable

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

GENERATION_ASSETS_TABLE_V2 = """
CREATE TABLE IF NOT EXISTS generation_assets (
    generation_id TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    role TEXT,
    position INTEGER NOT NULL,

    PRIMARY KEY (generation_id, asset_id, position),

    FOREIGN KEY (generation_id)
        REFERENCES generations(id)
        ON DELETE CASCADE,

    FOREIGN KEY (asset_id)
        REFERENCES assets(id)
        ON DELETE RESTRICT
);
"""

GENERATION_ASSETS_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_generation_assets_asset ON generation_assets(asset_id);
CREATE INDEX IF NOT EXISTS idx_generation_assets_generation ON generation_assets(generation_id);
"""

SCHEMA_ASSETS = f"""
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
{GENERATION_ASSETS_TABLE_V2}
{GENERATION_ASSETS_INDEXES}
"""

DB_SCHEMA_VERSION = 2

_SCHEMA_TABLES = {"generations", "assets", "generation_assets"}


def connect_db(db_path: str | Path) -> sqlite3.Connection:
    """统一业务 SQLite 连接：sqlite3.connect + PRAGMA foreign_keys = ON。

    SQLite 外键是 connection 级别设置，必须在每次连接时开启，
    保证 ON DELETE CASCADE / ON DELETE RESTRICT 真正生效。
    """
    conn = sqlite3.connect(Path(db_path).expanduser(), timeout=5.0)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }


def _create_schema_v2(conn: sqlite3.Connection) -> None:
    """全新数据库：创建 schema v2 并写入 user_version=2。"""
    conn.executescript(SCHEMA_GENERATIONS)
    conn.executescript(SCHEMA_ASSETS)
    conn.execute(f"PRAGMA user_version = {DB_SCHEMA_VERSION}")
    conn.commit()


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    """schema v1 → v2：只重建 generation_assets（加入外键）。

    仅保留 generation_id 仍存在于 generations 且 asset_id 仍存在于 assets
    的 relation；孤儿 relation 直接丢弃，不删除 generation / asset。
    全程事务化（all-or-nothing），结束后执行 foreign_key_check。
    """
    tables = _table_names(conn)
    required = {"generations", "assets", "generation_assets"}
    if not required <= tables:
        raise IncompatibleDatabaseError(
            "v1 数据库缺少 generations / assets / generation_assets 表，无法迁移。"
        )
    # PRAGMA foreign_keys 禁止在事务内部切换
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("ALTER TABLE generation_assets RENAME TO generation_assets_old")
        conn.execute("DROP INDEX IF EXISTS idx_generation_assets_asset")
        conn.execute("DROP INDEX IF EXISTS idx_generation_assets_generation")
        conn.execute(GENERATION_ASSETS_TABLE_V2)
        conn.execute("CREATE INDEX idx_generation_assets_asset ON generation_assets(asset_id)")
        conn.execute(
            "CREATE INDEX idx_generation_assets_generation "
            "ON generation_assets(generation_id)"
        )
        conn.execute(
            """
            INSERT INTO generation_assets
                (generation_id, asset_id, relation, role, position)
            SELECT old.generation_id, old.asset_id, old.relation, old.role, old.position
            FROM generation_assets_old AS old
            WHERE EXISTS (
                SELECT 1 FROM generations AS g WHERE g.id = old.generation_id
            )
            AND EXISTS (
                SELECT 1 FROM assets AS a WHERE a.id = old.asset_id
            )
            """
        )
        conn.execute("DROP TABLE generation_assets_old")
        conn.execute(f"PRAGMA user_version = {DB_SCHEMA_VERSION}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise IncompatibleDatabaseError(
            f"迁移后 foreign_key_check 发现 {len(violations)} 条违规，数据库状态异常。"
        )


# 后续 2 → 3、3 → 4 迁移在此扩展
_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    1: _migrate_v1_to_v2,
}


def initialize_db(db_path: str | Path) -> None:
    """初始化 / 迁移 ImageGen 2 SQLite 数据库；重复调用幂等。"""
    path = Path(db_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5.0)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version == DB_SCHEMA_VERSION:
            return
        if version == 0 and not (_table_names(conn) & _SCHEMA_TABLES):
            _create_schema_v2(conn)
            return
        migration = _MIGRATIONS.get(version)
        if migration is None:
            raise IncompatibleDatabaseError(
                f"数据库 {path} 的 schema 无法识别（user_version={version}），"
                f"当前仅支持升级到 ImageGen 2 schema v{DB_SCHEMA_VERSION}。"
                "为保护已有数据，程序不会自动删除或重建该数据库；"
                "请人工处理该文件后再启动。"
            )
        migration(conn)
        if conn.execute("PRAGMA user_version").fetchone()[0] != DB_SCHEMA_VERSION:
            raise IncompatibleDatabaseError(
                f"迁移失败：数据库 {path} 未达到 schema v{DB_SCHEMA_VERSION}。"
            )
    finally:
        conn.close()
