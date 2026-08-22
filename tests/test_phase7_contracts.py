"""Phase 7 contract 测试：分页、Detail 安全字段、references 恢复。"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from imagegen.models import GenerateRequest, GenerateResult
from imagegen.services.assets import AssetService
from imagegen.services.history import HistoryService

from .api_test_utils import ApiTestServer


def make_png_bytes(width: int = 16, height: int = 16) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (3, 4, 5)).save(buf, format="PNG")
    return buf.getvalue()


def make_result(gid: str, path: str = "out.png") -> GenerateResult:
    return GenerateResult(
        path=path,
        image_model_used="m",
        requested_size="16x16",
        actual_size="16x16",
        prompt_used="p",
        generation_id=gid,
    )


class TestHistoryPagination(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.history = HistoryService(self.base / "imagegen.db")
        for i in range(30):
            self.history.record(
                GenerateRequest(prompt=f"prompt-{i}"),
                make_result(gid=f"{i:032x}"),
            )
        self.server = ApiTestServer(
            config_path=self.base / "config.json", history_service=self.history
        )
        self.addCleanup(self.server.close)

    def test_first_page_has_more(self):
        status, _, data = self.server.json(
            "GET", "/api/v2/history?limit=24&offset=0"
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["count"], 24)
        self.assertTrue(data["has_more"])
        self.assertEqual(data["next_offset"], 24)
        for item in data["items"]:
            self.assertIn("thumbnail_url", item)
            self.assertIn("output_url", item)
            self.assertNotIn("prompt_used", item)
            self.assertNotIn("request", item)

    def test_last_page_no_more(self):
        status, _, data = self.server.json(
            "GET", "/api/v2/history?limit=24&offset=24"
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["count"], 6)
        self.assertFalse(data["has_more"])
        self.assertIsNone(data["next_offset"])

    def test_search_respects_pagination(self):
        status, _, data = self.server.json(
            "GET", "/api/v2/history?q=prompt-2&limit=5&offset=0"
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["count"], 5)
        self.assertTrue(data["has_more"])


class TestAssetPagination(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.assets = AssetService(
            db_path=self.base / "imagegen.db",
            asset_dir=self.base / "assets" / "references",
        )
        for i in range(5):
            self.assets.create_from_upload(
                make_png_bytes(), original_name=f"cat-{i}.png"
            )
        self.server = ApiTestServer(
            config_path=self.base / "config.json", asset_service=self.assets
        )
        self.addCleanup(self.server.close)

    def test_asset_pagination(self):
        status, _, data = self.server.json(
            "GET", "/api/v2/assets?limit=3&offset=0"
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["count"], 3)
        self.assertTrue(data["has_more"])
        self.assertEqual(data["next_offset"], 3)
        for item in data["items"]:
            self.assertIn("thumbnail_url", item)
            self.assertIn("content_url", item)

        status, _, data2 = self.server.json(
            "GET", "/api/v2/assets?limit=3&offset=3"
        )
        self.assertEqual(data2["count"], 2)
        self.assertFalse(data2["has_more"])
        self.assertIsNone(data2["next_offset"])


class TestHistoryDetailSafety(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.gid = "a" * 32
        self.history = HistoryService(self.base / "imagegen.db")
        request = GenerateRequest(
            prompt="safe prompt",
            size="1024x1024",
            model="m",
            quality="high",
            composition="portrait",
            translator="off",
            library_enabled=True,
            images=[r"D:\local\ref.png"],
            reference_roles=["character"],
            out=r"D:\local\out.png",
        )
        self.history.record(request, make_result(gid=self.gid))
        self.assets = AssetService(
            db_path=self.base / "imagegen.db",
            asset_dir=self.base / "assets" / "references",
        )
        rec = self.assets.create_from_upload(make_png_bytes(), original_name="r.png")
        self.assets.attach_to_generation(self.gid, rec.asset_id, "character", 0)
        self.server = ApiTestServer(
            config_path=self.base / "config.json",
            history_service=self.history,
            asset_service=self.assets,
        )
        self.addCleanup(self.server.close)

    def test_detail_request_allow_list_only(self):
        status, _, data = self.server.json(
            "GET", f"/api/v2/history/{self.gid}"
        )
        self.assertEqual(status, 200)
        item = data["item"]
        self.assertEqual(item["request"]["prompt"], "safe prompt")
        self.assertEqual(item["prompt_used"], "p")
        self.assertEqual(item["request"]["composition"], "portrait")
        self.assertEqual(item["request"]["library_enabled"], True)
        self.assertEqual(
            set(item["request"].keys()),
            {
                "prompt",
                "size",
                "model",
                "quality",
                "composition",
                "translator",
                "library_enabled",
            },
        )
        self.assertIn("thumbnail_url", item)
        self.assertIn("prompt_used", item)
        self.assertIn("warnings", item)
        for key in ("images", "reference_roles", "out", "path"):
            self.assertNotIn(key, item, key)
            self.assertNotIn(key, item["request"], key)
        text = json.dumps(data)
        for forbidden in ("output_path", "mirror_path", "file_path", "D:\\local"):
            self.assertNotIn(forbidden, text, forbidden)

    def test_detail_references_order_role_thumbnail(self):
        status, _, data = self.server.json(
            "GET", f"/api/v2/history/{self.gid}"
        )
        self.assertEqual(status, 200)
        refs = data["item"]["references"]
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["role"], "character")
        self.assertEqual(refs[0]["position"], 0)
        self.assertTrue(refs[0]["content_url"].endswith("/content"))
        self.assertTrue(refs[0]["thumbnail_url"].endswith("/thumbnail"))
        self.assertNotIn("file_path", json.dumps(refs))

    def test_missing_reference_asset_does_not_fail_detail(self):
        import sqlite3

        record = self.assets.list()[0]
        conn = sqlite3.connect(self.base / "imagegen.db")
        # 保留 generation_assets 引用，但删除 asset 行本身（模拟历史 asset 丢失）
        conn.execute("DELETE FROM assets WHERE id=?", (record.asset_id,))
        conn.commit()
        conn.close()
        status, _, data = self.server.json(
            "GET", f"/api/v2/history/{self.gid}"
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["item"]["references"], [])
        self.assertTrue(
            any("reference asset missing" in w for w in data["item"]["warnings"])
        )
