"""2.1.1 DB 关系测试：FK CASCADE / RESTRICT 真实生效。"""

from __future__ import annotations

import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from imagegen.errors import AssetInUseError
from imagegen.models import GenerateRequest, GenerateResult
from imagegen.services.assets import AssetService
from imagegen.services.history import HistoryService

from .api_test_utils import ApiTestServer


def make_png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (1, 2, 3)).save(buf, format="PNG")
    return buf.getvalue()


def make_result(gid: str) -> GenerateResult:
    return GenerateResult(
        path="out.png",
        image_model_used="m",
        requested_size="32x32",
        actual_size="32x32",
        prompt_used="p",
        generation_id=gid,
    )


class TestForeignKeyEnforcement(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.db_path = self.base / "imagegen.db"
        self.history = HistoryService(self.db_path)
        self.assets = AssetService(
            db_path=self.db_path,
            asset_dir=self.base / "assets" / "references",
        )

    def _relation_ids(self, generation_id: str) -> list[str]:
        conn = sqlite3.connect(self.db_path)
        try:
            return [
                row[0]
                for row in conn.execute(
                    "SELECT asset_id FROM generation_assets WHERE generation_id=?",
                    (generation_id,),
                )
            ]
        finally:
            conn.close()

    def test_delete_generation_cascades_relations(self):
        gid = "a" * 32
        self.history.record(GenerateRequest(prompt="p"), make_result(gid))
        asset = self.assets.create_from_upload(make_png_bytes())
        self.assets.attach_to_generation(gid, asset.asset_id, "character", 0)
        self.assertEqual(self._relation_ids(gid), [asset.asset_id])
        self.assertTrue(self.history.delete(gid))
        # 数据库级 ON DELETE CASCADE：relation 自动消失
        self.assertEqual(self._relation_ids(gid), [])
        # 引用消失后 asset 可删除
        self.assertTrue(self.assets.delete(asset.asset_id))

    def test_asset_in_use_raises(self):
        gid = "b" * 32
        self.history.record(GenerateRequest(prompt="p"), make_result(gid))
        asset = self.assets.create_from_upload(make_png_bytes())
        self.assets.attach_to_generation(gid, asset.asset_id, "character", 0)
        with self.assertRaises(AssetInUseError):
            self.assets.delete(asset.asset_id)

    def test_delete_generation_then_asset_succeeds(self):
        gid = "c" * 32
        self.history.record(GenerateRequest(prompt="p"), make_result(gid))
        asset = self.assets.create_from_upload(make_png_bytes())
        self.assets.attach_to_generation(gid, asset.asset_id, "character", 0)
        self.history.delete(gid)
        self.assertTrue(self.assets.delete(asset.asset_id))
        self.assertIsNone(self.assets.get(asset.asset_id))

    def test_http_delete_generation_then_asset(self):
        gid = "d" * 32
        self.history.record(GenerateRequest(prompt="p"), make_result(gid))
        asset = self.assets.create_from_upload(make_png_bytes())
        self.assets.attach_to_generation(gid, asset.asset_id, "character", 0)
        server = ApiTestServer(
            config_path=self.base / "config.json",
            history_service=self.history,
            asset_service=self.assets,
        )
        self.addCleanup(server.close)
        # 引用中 → 409 asset_in_use
        status, _, data = server.json(
            "DELETE", f"/api/v2/assets/{asset.asset_id}"
        )
        self.assertEqual(status, 409)
        self.assertEqual(data["error"]["type"], "asset_in_use")
        # 删除 generation → relation 自动消失 → asset 可删除
        status, _, _ = server.json("DELETE", f"/api/v2/history/{gid}")
        self.assertEqual(status, 200)
        status, _, _ = server.json("DELETE", f"/api/v2/assets/{asset.asset_id}")
        self.assertEqual(status, 200)
