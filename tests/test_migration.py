"""ImageGen 2 DB 基线测试：全新初始化 / 幂等打开 / 不兼容 DB 保护。"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from imagegen.errors import IncompatibleDatabaseError
from imagegen.models import GenerateRequest, GenerateResult
from imagegen.services.db import DB_SCHEMA_VERSION, initialize_db
from imagegen.services.history import HistoryService


def make_result(generation_id: str) -> GenerateResult:
    return GenerateResult(
        path="out.png",
        image_model_used="gemini-3-pro-image",
        seed=7,
        requested_size="1024x1024",
        actual_size="1024x1024",
        prompt_used="p",
        generation_id=generation_id,
    )


class TestInitializeDb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "imagegen.db"

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

    def test_fresh_db_creates_full_schema(self):
        initialize_db(self.db_path)
        self.assertTrue(self.db_path.is_file())
        conn = sqlite3.connect(self.db_path)
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            self.assertEqual(version, DB_SCHEMA_VERSION)
            cols = {
                row[1] for row in conn.execute("PRAGMA table_info(generations)")
            }
            self.assertIn("id", cols)
            self.assertIn("output_path", cols)
            self.assertIn("image_model_used", cols)
            self.assertIn("request_json", cols)
            self.assertNotIn("backend", cols)
        finally:
            conn.close()
        self.assertTrue(
            {"generations", "assets", "generation_assets"} <= self._tables()
        )

    def test_reopen_current_schema_idempotent(self):
        initialize_db(self.db_path)
        initialize_db(self.db_path)
        initialize_db(self.db_path)
        conn = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(
                conn.execute("PRAGMA user_version").fetchone()[0],
                DB_SCHEMA_VERSION,
            )
        finally:
            conn.close()
        # 重复打开后读写仍正常
        svc = HistoryService(self.db_path)
        rec = svc.record(GenerateRequest(prompt="x"), make_result("b" * 32))
        self.assertEqual(svc.get(rec.id).prompt, "x")

    def test_incompatible_existing_db_not_deleted(self):
        # 构造旧 lineage（含 backend 列、user_version=2）的数据库
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "CREATE TABLE generations ("
                " id TEXT PRIMARY KEY, created_at TEXT NOT NULL, output_path TEXT NOT NULL,"
                " backend TEXT, image_model_used TEXT, prompt TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT INTO generations VALUES (?,?,?,?,?,?)",
                ("a" * 32, "2026-08-22T00:00:00Z", r"D:\out.png", "vertex", "m", "p"),
            )
            conn.execute("PRAGMA user_version = 2")
            conn.commit()
        finally:
            conn.close()

        with self.assertRaises(IncompatibleDatabaseError):
            initialize_db(self.db_path)

        # 数据库与用户数据必须原样保留，不允许删除 / 重建
        self.assertTrue(self.db_path.is_file())
        conn = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 2)
            row = conn.execute("SELECT prompt FROM generations").fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "p")
        finally:
            conn.close()

    def test_incompatible_db_with_unknown_user_version(self):
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

    def test_schema_version_constant(self):
        self.assertEqual(DB_SCHEMA_VERSION, 1)
