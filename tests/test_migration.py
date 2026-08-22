"""ImageGen 2.1.1 DB 测试：schema v2 创建、v1→v2 迁移、孤儿 relation 清理。"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from imagegen.errors import IncompatibleDatabaseError
from imagegen.models import GenerateRequest, GenerateResult
from imagegen.services.db import DB_SCHEMA_VERSION, initialize_db
from imagegen.services.history import HistoryService


V1_GENERATIONS = """
CREATE TABLE generations (
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
"""

V1_ASSETS = """
CREATE TABLE assets (
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
"""

V1_GENERATION_ASSETS = """
CREATE TABLE generation_assets (
    generation_id TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    role TEXT,
    position INTEGER NOT NULL,
    PRIMARY KEY (generation_id, asset_id, position)
);
CREATE INDEX idx_generation_assets_asset ON generation_assets(asset_id);
CREATE INDEX idx_generation_assets_generation ON generation_assets(generation_id);
"""

G1 = "g" + "1" * 31
G2 = "g" + "2" * 31
G_MISSING = "g" + "9" * 31
A1 = "a" + "1" * 31
A2 = "a" + "2" * 31
A_MISSING = "a" + "9" * 31


def make_result(generation_id: str) -> GenerateResult:
    return GenerateResult(
        path="out.png",
        image_model_used="gemini-3-pro-image",
        requested_size="1024x1024",
        actual_size="1024x1024",
        prompt_used="p",
        generation_id=generation_id,
    )


class TestSchemaV2(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "imagegen.db"

    def _version(self) -> int:
        conn = sqlite3.connect(self.db_path)
        try:
            return conn.execute("PRAGMA user_version").fetchone()[0]
        finally:
            conn.close()

    def _tables(self) -> set[str]:
        conn = sqlite3.connect(self.db_path)
        try:
            return {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        finally:
            conn.close()

    def test_fresh_db_creates_schema_v2(self):
        initialize_db(self.db_path)
        self.assertTrue(self.db_path.is_file())
        self.assertEqual(self._version(), 2)
        self.assertTrue(
            {"generations", "assets", "generation_assets"} <= self._tables()
        )
        conn = sqlite3.connect(self.db_path)
        try:
            gen_cols = {
                row[1] for row in conn.execute("PRAGMA table_info(generations)")
            }
            self.assertNotIn("backend", gen_cols)
            # generation_assets 必须带真实外键
            fks = conn.execute(
                "PRAGMA foreign_key_list(generation_assets)"
            ).fetchall()
            fk_targets = {row[2] for row in fks}
            self.assertEqual(fk_targets, {"generations", "assets"})
            actions = {(row[2], row[6], row[7]) for row in fks}
            self.assertIn(("generations", "CASCADE", "NONE"), actions)
            self.assertIn(("assets", "RESTRICT", "NONE"), actions)
        finally:
            conn.close()

    def test_initialize_idempotent(self):
        initialize_db(self.db_path)
        initialize_db(self.db_path)
        initialize_db(self.db_path)
        self.assertEqual(self._version(), 2)
        svc = HistoryService(self.db_path)
        rec = svc.record(GenerateRequest(prompt="x"), make_result("b" * 32))
        self.assertEqual(svc.get(rec.id).prompt, "x")

    def test_schema_version_constant(self):
        self.assertEqual(DB_SCHEMA_VERSION, 2)

    def test_unrecognized_version_incompatible(self):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("CREATE TABLE unrelated (x TEXT)")
            conn.execute("PRAGMA user_version = 99")
            conn.commit()
        finally:
            conn.close()
        with self.assertRaises(IncompatibleDatabaseError):
            initialize_db(self.db_path)
        self.assertTrue(self.db_path.is_file())
        self.assertEqual(self._version(), 99)

    def test_v0_with_business_tables_incompatible(self):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("CREATE TABLE generations (id TEXT PRIMARY KEY)")
            conn.commit()
        finally:
            conn.close()
        with self.assertRaises(IncompatibleDatabaseError):
            initialize_db(self.db_path)


class TestV1ToV2Migration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "imagegen.db"

    def _make_v1_db(self) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.executescript(V1_GENERATIONS)
            conn.executescript(V1_ASSETS)
            conn.executescript(V1_GENERATION_ASSETS)
            for gid, prompt, used in ((G1, "p1", "used1"), (G2, "p2", "used2")):
                conn.execute(
                    "INSERT INTO generations (id, created_at, output_path,"
                    " image_model_used, prompt, prompt_used, seed, requested_size,"
                    " actual_size, warnings_json, metadata_json, request_json)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        gid,
                        "2026-08-22T00:00:00Z",
                        r"D:\out.png",
                        "m",
                        prompt,
                        used,
                        None,
                        "1024x1024",
                        "1024x1024",
                        "[]",
                        "{}",
                        "{}",
                    ),
                )
            for asset_id in (A1, A2):
                conn.execute(
                    "INSERT INTO assets (id, created_at, kind, source, file_path,"
                    " original_name, mime_type, size_bytes, width, height, sha256,"
                    " metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        asset_id,
                        "2026-08-22T00:00:00Z",
                        "reference",
                        "upload",
                        r"D:\assets.png",
                        "r.png",
                        "image/png",
                        10,
                        10,
                        10,
                        "abc",
                        "{}",
                    ),
                )
            # 2 条有效 relation + 1 条孤儿 generation relation + 1 条孤儿 asset relation
            conn.executemany(
                "INSERT INTO generation_assets"
                " (generation_id, asset_id, relation, role, position)"
                " VALUES (?,?,?,?,?)",
                [
                    (G1, A1, "reference", "character", 0),
                    (G2, A2, "reference", "style", 0),
                    (G_MISSING, A1, "reference", "orphan-gen", 0),
                    (G1, A_MISSING, "reference", "orphan-asset", 1),
                ],
            )
            conn.execute("PRAGMA user_version = 1")
            conn.commit()
        finally:
            conn.close()

    def test_v1_migrates_to_v2_cleaning_orphans(self):
        self._make_v1_db()
        initialize_db(self.db_path)
        conn = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 2)
            # generations / assets 全部保留（迁移不删除任何数据）
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM generations").fetchone()[0], 2
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0], 2
            )
            rows = conn.execute(
                "SELECT generation_id, asset_id, role, position"
                " FROM generation_assets ORDER BY position"
            ).fetchall()
            # 仅有效 relation 保留；两类孤儿 relation 均被清理
            self.assertEqual(
                rows,
                [
                    (G1, A1, "character", 0),
                    (G2, A2, "style", 0),
                ],
            )
        finally:
            conn.close()

    def test_v1_migration_preserves_prompts(self):
        self._make_v1_db()
        svc = HistoryService(self.db_path)
        rec = svc.get(G1)
        self.assertIsNotNone(rec)
        self.assertEqual(rec.prompt, "p1")
        self.assertEqual(rec.prompt_used, "used1")
