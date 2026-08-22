"""Schema v2 迁移测试：空库 / Phase 5A v1 升级 / 幂等。"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from imagegen.models import GenerateRequest, GenerateResult
from imagegen.services.db import CURRENT_SCHEMA_VERSION, migrate_db
from imagegen.services.history import HistoryService


V1_GENERATIONS_SCHEMA = """
CREATE TABLE generations (
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
"""


def make_result(generation_id: str) -> GenerateResult:
    return GenerateResult(
        path="out.png",
        backend="vertex",
        image_model_used="gemini-3-pro-image",
        seed=7,
        requested_size="1024x1024",
        actual_size="1024x1024",
        prompt_used="p",
        generation_id=generation_id,
    )


class TestMigration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "imagegen.db"

    def _make_v1_db(self) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.executescript(V1_GENERATIONS_SCHEMA)
            conn.execute(
                "INSERT INTO generations (id, created_at, output_path, backend, "
                " image_model_used, prompt, prompt_used, seed, requested_size, actual_size, "
                " warnings_json, metadata_json, request_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "a" * 32,
                    "2026-08-22T03:31:46.123Z",
                    r"D:\out.png",
                    "vertex",
                    "gemini-3-pro-image",
                    "sakura princess",
                    "a princess",
                    7,
                    "1024x1024",
                    "1024x1024",
                    "[]",
                    "{}",
                    '{"prompt": "sakura princess"}',
                ),
            )
            conn.execute("PRAGMA user_version = 1")
            conn.commit()
        finally:
            conn.close()

    def test_fresh_db_creates_full_v2_schema(self):
        migrate_db(self.db_path)
        conn = sqlite3.connect(self.db_path)
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            self.assertEqual(version, 2)
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self.assertTrue({"generations", "assets", "generation_assets"} <= tables)
            asset_cols = [row[1] for row in conn.execute("PRAGMA table_info(assets)")]
            for col in (
                "id", "created_at", "kind", "source", "file_path", "original_name",
                "mime_type", "size_bytes", "width", "height", "sha256", "metadata_json",
            ):
                self.assertIn(col, asset_cols, col)
            link_cols = [
                row[1] for row in conn.execute("PRAGMA table_info(generation_assets)")
            ]
            for col in ("generation_id", "asset_id", "relation", "role", "position"):
                self.assertIn(col, link_cols, col)
        finally:
            conn.close()

    def test_v1_db_upgrades_and_preserves_generations(self):
        self._make_v1_db()
        migrate_db(self.db_path)
        conn = sqlite3.connect(self.db_path)
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            self.assertEqual(version, 2)
            row = conn.execute("SELECT * FROM generations WHERE id=?", ("a" * 32,)).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[5], "sakura princess")
            tables = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            self.assertIn("assets", tables)
            self.assertIn("generation_assets", tables)
        finally:
            conn.close()

    def test_history_service_upgrades_v1_db(self):
        self._make_v1_db()
        svc = HistoryService(self.db_path)
        conn = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 2)
        finally:
            conn.close()
        record = svc.get("a" * 32)
        self.assertIsNotNone(record)
        self.assertEqual(record.prompt, "sakura princess")
        self.assertEqual(record.output_path, r"D:\out.png")

    def test_migration_idempotent(self):
        migrate_db(self.db_path)
        migrate_db(self.db_path)
        migrate_db(self.db_path)
        conn = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 2)
            # 升级后写入仍然正常
            svc = HistoryService(self.db_path)
            rec = svc.record(GenerateRequest(prompt="x"), make_result("b" * 32))
            self.assertEqual(svc.get(rec.id).prompt, "x")
        finally:
            conn.close()

    def test_current_schema_version_constant(self):
        self.assertEqual(CURRENT_SCHEMA_VERSION, 2)
