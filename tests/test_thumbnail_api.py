"""Thumbnail HTTP API 测试：generation / asset 缩略图、ETag / 304、安全边界。"""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from imagegen.api import OutputRegistry
from imagegen.models import GenerateRequest, GenerateResult
from imagegen.services.assets import AssetService
from imagegen.services.history import HistoryService

from .api_test_utils import ApiTestServer


def make_png_bytes(width: int = 96, height: int = 96) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (12, 34, 56)).save(buf, format="PNG")
    return buf.getvalue()


class TestGenerationThumbnail(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.out_file = self.base / "out.png"
        self.out_file.write_bytes(make_png_bytes())
        self.gid = "a" * 32
        self.history = HistoryService(self.base / "imagegen.db")
        self.history.record(
            GenerateRequest(prompt="p"),
            GenerateResult(
                path=str(self.out_file),
                image_model_used="m",
                requested_size="96x96",
                actual_size="96x96",
                prompt_used="p",
                generation_id=self.gid,
            ),
        )
        self.server = ApiTestServer(
            config_path=self.base / "config.json",
            history_service=self.history,
            output_registry=OutputRegistry(),
        )
        self.addCleanup(self.server.close)
        self.url = f"/api/v2/outputs/{self.gid}/thumbnail"

    def test_thumbnail_webp_with_validators(self):
        status, headers, body = self.server.request("GET", self.url)
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "image/webp")
        self.assertTrue(headers.get("ETag"))
        self.assertTrue(headers.get("Last-Modified"))
        self.assertEqual(
            headers.get("Cache-Control"), "private, max-age=31536000, immutable"
        )
        self.assertGreater(len(body), 0)

    def test_if_none_match_304_empty_body(self):
        _, headers, _ = self.server.request("GET", self.url)
        etag = headers["ETag"]
        status, headers2, body = self.server.request(
            "GET", self.url, headers={"If-None-Match": etag}
        )
        self.assertEqual(status, 304)
        self.assertEqual(body, b"")
        self.assertEqual(headers2.get("ETag"), etag)

    def test_unknown_generation_404(self):
        status, _, _ = self.server.request(
            "GET", "/api/v2/outputs/" + "b" * 32 + "/thumbnail"
        )
        self.assertEqual(status, 404)

    def test_original_output_has_browser_cache_headers(self):
        status, headers, body = self.server.request(
            "GET", "/api/v2/outputs/" + self.gid
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Cache-Control"), "private, max-age=3600")
        self.assertTrue(headers.get("ETag"))
        self.assertEqual(body, self.out_file.read_bytes())

    def test_no_arbitrary_path_read(self):
        for path in (
            "/api/v2/outputs/whatever/thumbnail?path=C%3A%5CWindows%5Cwin.ini",
            "/api/v2/outputs/whatever/thumbnail?file=" + self.base.as_posix(),
            "/api/v2/outputs/" + self.gid + "/thumbnail/..%2F..%2Fconfig.json",
        ):
            status, _, _ = self.server.request("GET", path)
            self.assertEqual(status, 404, path)

    def test_delete_history_keeps_output_but_clears_preview(self):
        self.server.request("GET", self.url)
        cache_dir = self.base / "cache" / "previews" / "generations"
        self.assertTrue(list(cache_dir.glob(self.gid + "__*")))
        status, _, _ = self.server.request(
            "DELETE", "/api/v2/history/" + self.gid
        )
        self.assertEqual(status, 200)
        self.assertTrue(self.out_file.is_file())  # 原图保留
        self.assertEqual(list(cache_dir.glob(self.gid + "__*")), [])


class TestAssetThumbnail(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.assets = AssetService(
            db_path=self.base / "imagegen.db",
            asset_dir=self.base / "assets" / "references",
        )
        self.record = self.assets.create_from_upload(
            make_png_bytes(), original_name="a.png"
        )
        self.server = ApiTestServer(
            config_path=self.base / "config.json", asset_service=self.assets
        )
        self.addCleanup(self.server.close)
        self.url = f"/api/v2/assets/{self.record.asset_id}/thumbnail"

    def test_asset_thumbnail_webp(self):
        status, headers, body = self.server.request("GET", self.url)
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "image/webp")
        self.assertTrue(headers.get("ETag"))
        self.assertGreater(len(body), 0)

    def test_asset_content_immutable_cache(self):
        status, headers, body = self.server.request(
            "GET", f"/api/v2/assets/{self.record.asset_id}/content"
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            headers.get("Cache-Control"), "private, max-age=31536000, immutable"
        )
        self.assertTrue(headers.get("ETag"))
        self.assertEqual(body, Path(self.record.file_path).read_bytes())

    def test_delete_asset_clears_preview_and_file(self):
        self.server.request("GET", self.url)
        cache_dir = self.base / "cache" / "previews" / "assets"
        self.assertTrue(list(cache_dir.glob(self.record.asset_id + "__*")))
        status, _, _ = self.server.request(
            "DELETE", f"/api/v2/assets/{self.record.asset_id}"
        )
        self.assertEqual(status, 200)
        self.assertFalse(Path(self.record.file_path).exists())
        self.assertEqual(list(cache_dir.glob(self.record.asset_id + "__*")), [])

    def test_unknown_asset_404(self):
        status, _, _ = self.server.request(
            "GET", "/api/v2/assets/deadbeef/thumbnail"
        )
        self.assertEqual(status, 404)
