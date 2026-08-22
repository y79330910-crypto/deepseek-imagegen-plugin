"""HistoryService / SQLite 持久化测试（临时 DB，不触碰真实用户数据）。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from imagegen.models import GenerateRequest, GenerateResult
from imagegen.services.history import HistoryRecord, HistoryService, utc_now_iso


def make_result(path="out.png", generation_id="a" * 32, prompt_used="p"):
    return GenerateResult(
        path=path,
        backend="vertex",
        image_model_used="gemini-3-pro-image",
        seed=7,
        requested_size="1024x1024",
        actual_size="1024x1024",
        prompt_used=prompt_used,
        warnings=["w1"],
    )


class TestHistoryService(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "imagegen.db"
        self.svc = HistoryService(self.db_path)

    def test_db_created_and_user_version_2(self):
        import sqlite3

        self.assertTrue(self.db_path.is_file())
        conn = sqlite3.connect(self.db_path)
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(version, 2)

    def test_record_get_round_trip(self):
        req = GenerateRequest(prompt="hello", size="1024x1024", seed=7)
        rec = self.svc.record(req, make_result())
        got = self.svc.get(rec.id)
        self.assertIsNotNone(got)
        self.assertEqual(got.id, rec.id)
        self.assertEqual(got.prompt, "hello")
        self.assertEqual(got.seed, 7)
        self.assertEqual(got.warnings, ["w1"])
        self.assertEqual(got.request["prompt"], "hello")
        self.assertEqual(got.request["size"], "1024x1024")
        self.assertEqual(got.backend, "vertex")
        self.assertEqual(got.created_at, rec.created_at)

    def test_request_json_round_trip(self):
        req = GenerateRequest(
            prompt="multi",
            images=["a.png", "b.png"],
            reference_roles=["character", "outfit"],
            quality="high",
            size_policy="exact",
        )
        rec = self.svc.record(req, make_result())
        restored = GenerateRequest.from_dict(rec.request)
        self.assertEqual(restored.prompt, "multi")
        self.assertEqual(restored.images, ["a.png", "b.png"])
        self.assertEqual(restored.reference_roles, ["character", "outfit"])
        self.assertEqual(restored.quality, "high")
        self.assertEqual(restored.size_policy, "exact")

    def test_list_order_and_pagination(self):
        for i in range(3):
            self.svc.record(
                GenerateRequest(prompt=f"prompt-{i}"),
                make_result(generation_id=chr(ord("a") + i) * 32, prompt_used=f"p{i}"),
            )
        items = self.svc.list(limit=2, offset=0)
        self.assertEqual(len(items), 2)
        items2 = self.svc.list(limit=2, offset=2)
        self.assertEqual(len(items2), 1)
        # created_at DESC
        self.assertGreaterEqual(items[0].created_at, items[1].created_at)

    def test_search_by_prompt_and_prompt_used(self):
        self.svc.record(GenerateRequest(prompt="樱花公主"), make_result(prompt_used="sakura princess"))
        self.svc.record(GenerateRequest(prompt="海边少年"), make_result(prompt_used="beach boy"))
        hits = self.svc.list(query="樱花")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].prompt, "樱花公主")
        hits2 = self.svc.list(query="beach")
        self.assertEqual(len(hits2), 1)
        self.assertEqual(hits2[0].prompt, "海边少年")

    def test_delete(self):
        rec = self.svc.record(GenerateRequest(prompt="x"), make_result())
        self.assertTrue(self.svc.delete(rec.id))
        self.assertIsNone(self.svc.get(rec.id))
        self.assertFalse(self.svc.delete(rec.id))

    def test_limit_capped_at_max(self):
        for i in range(5):
            self.svc.record(
                GenerateRequest(prompt=f"p{i}"),
                make_result(generation_id=chr(ord("a") + i) * 32),
            )
        items = self.svc.list(limit=999)
        self.assertLessEqual(len(items), 100)

    def test_utc_now_iso_format(self):
        self.assertRegex(utc_now_iso(), r"^\d{4}-\d{2}-\d{2}T.*Z$")


class TestHistoryRecord(unittest.TestCase):
    def test_from_row_and_to_dict(self):
        rec = HistoryRecord(
            id="x" * 32,
            created_at="2026-08-22T03:31:46.123Z",
            output_path=r"D:\out.png",
            backend="vertex",
            image_model_used="m",
            prompt="p",
            prompt_used="pp",
            seed=1,
            requested_size="1x1",
            actual_size="1x1",
            warnings=["w"],
            metadata={"a": 1},
            request={"prompt": "p"},
        )
        d = rec.to_dict()
        self.assertEqual(d["id"], "x" * 32)
        self.assertEqual(d["output_path"], r"D:\out.png")
        self.assertEqual(d["warnings"], ["w"])
        self.assertEqual(d["request"]["prompt"], "p")
