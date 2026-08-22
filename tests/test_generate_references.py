"""Asset Reference 生成集成：references → resolver → GenerateRequest → generation_assets。"""

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


def make_png_bytes(width: int = 16, height: int = 16) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (3, 4, 5)).save(buf, format="PNG")
    return buf.getvalue()


def make_result(generation_id: str = "c" * 32) -> GenerateResult:
    return GenerateResult(
        path="out.png",
        image_model_used="gemini-3-pro-image",
        seed=1,
        requested_size="16x16",
        actual_size="16x16",
        prompt_used="p",
        generation_id=generation_id,
    )


class FakeEngine:
    def __init__(self, generation_id: str = "c" * 32):
        self.generation_id = generation_id
        self.last_request = None

    def generate(self, request):
        self.last_request = request
        return make_result(generation_id=self.generation_id)


class TestGenerateReferences(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.db_path = base / "imagegen.db"
        self.assets = AssetService(
            db_path=self.db_path,
            asset_dir=base / "assets" / "references",
        )
        self.history = HistoryService(self.db_path)
        self.engine = FakeEngine()
        gen_svc = GenerationService(engine=self.engine, history_service=self.history)
        self.server = ApiTestServer(
            config_path=base / "config.json",
            generation_service=gen_svc,
            history_service=self.history,
            asset_service=self.assets,
        )
        self.addCleanup(self.server.close)

    def test_references_generate_writes_generation_assets(self):
        a = self.assets.create_from_upload(make_png_bytes(), original_name="a.png")
        b = self.assets.create_from_upload(make_png_bytes(), original_name="b.png")
        status, _, data = self.server.json(
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
        request = self.engine.last_request
        self.assertEqual(request.images, [a.file_path, b.file_path])
        self.assertEqual(request.reference_roles, ["character", "style"])
        links = self.assets.list_for_generation(self.engine.generation_id)
        self.assertEqual([link.asset_id for link in links], [a.asset_id, b.asset_id])
        self.assertEqual([link.role for link in links], ["character", "style"])
        self.assertEqual([link.position for link in links], [0, 1])
        self.assertNotIn("references", data)

    def test_legacy_images_still_work(self):
        status, _, data = self.server.json(
            "POST",
            "/api/v2/generate",
            {"prompt": "x", "images": [r"D:\old\a.png"]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.engine.last_request.images, [r"D:\old\a.png"])
        self.assertEqual(
            self.assets.list_for_generation(self.engine.generation_id), []
        )

    def test_unknown_asset_404(self):
        status, _, data = self.server.json(
            "POST",
            "/api/v2/generate",
            {"prompt": "x", "references": [{"asset_id": "deadbeef"}]},
        )
        self.assertEqual(status, 404)
        self.assertEqual(data["error"]["type"], "not_found")

    def test_more_than_four_references_400(self):
        rec = self.assets.create_from_upload(make_png_bytes())
        refs = [{"asset_id": rec.asset_id, "role": "character"} for _ in range(5)]
        status, _, data = self.server.json(
            "POST", "/api/v2/generate", {"prompt": "x", "references": refs}
        )
        self.assertEqual(status, 400)
        self.assertEqual(data["error"]["type"], "validation_error")

    def test_relation_persistence_failure_keeps_generation_success(self):
        rec = self.assets.create_from_upload(make_png_bytes())

        def boom(*args, **kwargs):
            raise RuntimeError("db locked")

        self.assets.attach_to_generation = boom
        status, _, data = self.server.json(
            "POST",
            "/api/v2/generate",
            {
                "prompt": "x",
                "references": [{"asset_id": rec.asset_id, "role": "character"}],
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(
            any("reference relation persistence failed" in w for w in data["warnings"])
        )
