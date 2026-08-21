"""Output Registry 与 GET /api/v1/outputs/{generation_id} 集成测试。"""

from __future__ import annotations

import io
import unittest
from pathlib import Path

from imagegen.api import OutputRegistry

from .api_test_utils import ApiTestServer


def make_png_bytes() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


class TestOutputRegistry(unittest.TestCase):
    def test_register_and_get(self):
        registry = OutputRegistry(max_entries=4)
        registry.register("a" * 32, r"D:\a.png")
        registry.register("b" * 32, r"D:\b.png")
        self.assertEqual(registry.get("a" * 32), r"D:\a.png")
        self.assertEqual(registry.get("b" * 32), r"D:\b.png")
        self.assertIsNone(registry.get("c" * 32))

    def test_max_entries_evicts_oldest(self):
        registry = OutputRegistry(max_entries=2)
        registry.register("1" * 32, "a")
        registry.register("2" * 32, "b")
        registry.register("3" * 32, "c")
        self.assertIsNone(registry.get("1" * 32))
        self.assertEqual(registry.get("2" * 32), "b")
        self.assertEqual(registry.get("3" * 32), "c")


class TestOutputRoute(unittest.TestCase):
    def setUp(self):
        self.png = make_png_bytes()
        self.tmp = Path(self._tempdir())
        self.out_file = self.tmp / "result.png"
        self.out_file.write_bytes(self.png)
        self.registry = OutputRegistry()
        self.registry.register("a" * 32, str(self.out_file))
        self.server = ApiTestServer(output_registry=self.registry)
        self.addCleanup(self.server.close)

    def _tempdir(self):
        import tempfile

        handle = tempfile.TemporaryDirectory()
        self.addCleanup(handle.cleanup)
        return handle.name

    def test_get_registered_output(self):
        status, headers, body = self.server.request(
            "GET", f"/api/v1/outputs/{'a' * 32}"
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "image/png")
        self.assertEqual(body, self.png)

    def test_unknown_generation_id_404(self):
        status, _, data = self.server.json("GET", f"/api/v1/outputs/{'b' * 32}")
        self.assertEqual(status, 404)
        self.assertEqual(data["error"]["type"], "not_found")

    def test_deleted_file_404(self):
        self.out_file.unlink()
        status, _, data = self.server.json("GET", f"/api/v1/outputs/{'a' * 32}")
        self.assertEqual(status, 404)
        self.assertEqual(data["error"]["type"], "not_found")

    def test_no_path_query_api(self):
        status, _, _ = self.server.json(
            "GET", "/api/v1/outputs?path=C%3A%5CWindows%5Cwin.ini"
        )
        self.assertEqual(status, 404)
