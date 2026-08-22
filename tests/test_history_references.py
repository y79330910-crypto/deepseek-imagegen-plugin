"""History detail references 回归：generation_assets 持久化 + server 重启后仍可读取。"""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from imagegen.models import GenerateResult
from imagegen.services.assets import AssetService
from imagegen.services.generation import GenerationService
from imagegen.services.history import HistoryService

from .api_test_utils import ApiTestServer


def make_png_bytes(width: int = 12, height: int = 12) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (6, 7, 8)).save(buf, format="PNG")
    return buf.getvalue()


def make_result(generation_id: str) -> GenerateResult:
    return GenerateResult(
        path="out.png",
        image_model_used="gemini-3-pro-image",
        requested_size="12x12",
        actual_size="12x12",
        prompt_used="p",
        generation_id=generation_id,
    )


class FixedGenerationIdEngine:
    def __init__(self, generation_id: str):
        self.generation_id = generation_id

    def generate(self, request):
        return make_result(generation_id=self.generation_id)


class TestHistoryReferences(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.config_path = base / "config.json"
        self.db_path = base / "imagegen.db"
        self.assets = AssetService(
            db_path=self.db_path,
            asset_dir=base / "assets" / "references",
        )
        self.history = HistoryService(self.db_path)
        self.generation_id = "9" * 32
        self.engine = FixedGenerationIdEngine(self.generation_id)

    def _server(self):
        gen_svc = GenerationService(engine=self.engine, history_service=self.history)
        return ApiTestServer(
            config_path=self.config_path,
            generation_service=gen_svc,
            history_service=self.history,
            asset_service=self.assets,
        )

    def test_history_detail_references_survive_restart(self):
        a = self.assets.create_from_upload(make_png_bytes(), original_name="a.png")
        b = self.assets.create_from_upload(make_png_bytes(), original_name="b.png")
        server1 = self._server()
        self.addCleanup(server1.close)
        status, _, data = server1.json(
            "POST",
            "/api/v2/generate",
            {
                "prompt": "hello",
                "references": [
                    {"asset_id": a.asset_id, "role": "character"},
                    {"asset_id": b.asset_id, "role": "style"},
                ],
            },
        )
        self.assertEqual(status, 200)

        # 模拟重启：新 registry + 同一 DB / asset 目录
        server2 = self._server()
        self.addCleanup(server2.close)
        status, _, data = server2.json("GET", f"/api/v2/history/{self.generation_id}")
        self.assertEqual(status, 200)
        refs = data["item"]["references"]
        self.assertEqual(
            [r["asset_id"] for r in refs], [a.asset_id, b.asset_id]
        )
        self.assertEqual([r["role"] for r in refs], ["character", "style"])
        self.assertEqual([r["position"] for r in refs], [0, 1])
        for ref in refs:
            self.assertEqual(
                ref["content_url"], f"/api/v2/assets/{ref['asset_id']}/content"
            )
            self.assertNotIn("file_path", ref)
        # asset content 仍可读取
        status, headers, body = server2.request(
            "GET", f"/api/v2/assets/{a.asset_id}/content"
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, Path(a.file_path).read_bytes())

    def test_list_does_not_include_references(self):
        a = self.assets.create_from_upload(make_png_bytes())
        server = self._server()
        self.addCleanup(server.close)
        server.json(
            "POST",
            "/api/v2/generate",
            {
                "prompt": "hello",
                "references": [{"asset_id": a.asset_id, "role": "character"}],
            },
        )
        status, _, data = server.json("GET", "/api/v2/history")
        self.assertEqual(status, 200)
        self.assertEqual(data["count"], 1)
        self.assertNotIn("references", data["items"][0])

    def test_legacy_generation_has_empty_references(self):
        server = self._server()
        self.addCleanup(server.close)
        server.json(
            "POST",
            "/api/v2/generate",
            {"prompt": "x", "images": [r"D:\old\a.png"]},
        )
        status, _, data = server.json("GET", f"/api/v2/history/{self.generation_id}")
        self.assertEqual(status, 200)
        self.assertEqual(data["item"]["references"], [])
