"""History HTTP API 与 Output 回退测试。"""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from imagegen.api import OutputRegistry
from imagegen.models import GenerateRequest, GenerateResult
from imagegen.services.generation import GenerationService
from imagegen.services.history import HistoryService

from .api_test_utils import ApiTestServer


def make_png_bytes() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (5, 6, 7)).save(buf, format="PNG")
    return buf.getvalue()


def make_result(gid="a" * 32, path="out.png"):
    return GenerateResult(
        path=path,
        image_model_used="gemini-3-pro-image",
        seed=7,
        requested_size="1024x1024",
        actual_size="1024x1024",
        prompt_used="a princess under cherry blossoms",
        generation_id=gid,
    )


def make_history(db_path: Path) -> HistoryService:
    svc = HistoryService(db_path)
    svc.record(GenerateRequest(prompt="sakura princess"), make_result(gid="a" * 32))
    svc.record(GenerateRequest(prompt="beach boy"), make_result(gid="b" * 32))
    return svc


class TestHistoryApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.history = make_history(Path(self.tmp.name) / "imagegen.db")
        self.server = ApiTestServer(
            config_path=Path(self.tmp.name) / "config.json",
            history_service=self.history,
        )
        self.addCleanup(self.server.close)

    def test_list_history(self):
        status, _, data = self.server.json("GET", "/api/v1/history")
        self.assertEqual(status, 200)
        self.assertEqual(data["count"], 2)
        ids = {item["generation_id"] for item in data["items"]}
        self.assertEqual(ids, {"a" * 32, "b" * 32})
        for item in data["items"]:
            self.assertNotIn("output_path", item)
            self.assertIn("output_url", item)
            self.assertIn("prompt", item)

    def test_get_history_item(self):
        status, _, data = self.server.json("GET", f"/api/v1/history/{'a' * 32}")
        self.assertEqual(status, 200)
        self.assertEqual(data["item"]["generation_id"], "a" * 32)
        self.assertEqual(data["item"]["prompt"], "sakura princess")
        self.assertEqual(data["item"]["output_url"], f"/api/v1/outputs/{'a' * 32}")

    def test_get_unknown_history_404(self):
        status, _, data = self.server.json("GET", f"/api/v1/history/{'c' * 32}")
        self.assertEqual(status, 404)
        self.assertEqual(data["error"]["type"], "not_found")

    def test_search_history(self):
        status, _, data = self.server.json("GET", "/api/v1/history?q=beach")
        self.assertEqual(status, 200)
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["items"][0]["generation_id"], "b" * 32)

    def test_limit_and_offset_validation(self):
        status, _, data = self.server.json("GET", "/api/v1/history?limit=0")
        self.assertEqual(status, 400)
        self.assertEqual(data["error"]["type"], "validation_error")
        status, _, data = self.server.json("GET", "/api/v1/history?limit=101")
        self.assertEqual(status, 400)
        status, _, data = self.server.json("GET", "/api/v1/history?offset=-1")
        self.assertEqual(status, 400)
        status, _, data = self.server.json("GET", "/api/v1/history?limit=abc")
        self.assertEqual(status, 400)

    def test_delete_history(self):
        status, _, data = self.server.json("DELETE", f"/api/v1/history/{'a' * 32}")
        self.assertEqual(status, 200)
        self.assertTrue(data["deleted"])
        status, _, _ = self.server.json("GET", f"/api/v1/history/{'a' * 32}")
        self.assertEqual(status, 404)


class TestOutputFallback(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out_file = Path(self.tmp.name) / "result.png"
        self.out_file.write_bytes(make_png_bytes())
        self.history = HistoryService(Path(self.tmp.name) / "imagegen.db")
        self.history.record(
            GenerateRequest(prompt="p"),
            make_result(gid="d" * 32, path=str(self.out_file)),
        )

    def test_registry_miss_falls_back_to_history(self):
        # 空 registry（模拟 server 重启），历史记录仍可提供文件
        server = ApiTestServer(
            config_path=Path(self.tmp.name) / "config.json",
            history_service=self.history,
            output_registry=OutputRegistry(),
        )
        self.addCleanup(server.close)
        status, headers, body = server.request("GET", f"/api/v1/outputs/{'d' * 32}")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "image/png")
        self.assertEqual(body, self.out_file.read_bytes())

    def test_history_output_missing_file_404(self):
        self.out_file.unlink()
        server = ApiTestServer(
            config_path=Path(self.tmp.name) / "config.json",
            history_service=self.history,
            output_registry=OutputRegistry(),
        )
        self.addCleanup(server.close)
        status, _, _ = server.json("GET", f"/api/v1/outputs/{'d' * 32}")
        self.assertEqual(status, 404)

    def test_cannot_fetch_arbitrary_path(self):
        server = ApiTestServer(
            config_path=Path(self.tmp.name) / "config.json",
            history_service=self.history,
            output_registry=OutputRegistry(),
        )
        self.addCleanup(server.close)
        # 未注册 / 不在历史里的 id 不能读取
        status, _, _ = server.json("GET", f"/api/v1/outputs/{'f' * 32}")
        self.assertEqual(status, 404)
        # path 参数完全不可用
        status, _, _ = server.json(
            "GET",
            "/api/v1/outputs?path=C%3A%5CWindows%5Cwin.ini",
        )
        self.assertEqual(status, 404)


class TestDeleteUnregistersOutput(unittest.TestCase):
    def test_delete_history_makes_output_404(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        out_file = Path(tmp.name) / "r.png"
        out_file.write_bytes(make_png_bytes())
        history = HistoryService(Path(tmp.name) / "imagegen.db")
        history.record(
            GenerateRequest(prompt="p"),
            make_result(gid="e" * 32, path=str(out_file)),
        )
        registry = OutputRegistry()
        registry.register("e" * 32, str(out_file))
        server = ApiTestServer(
            config_path=Path(tmp.name) / "config.json",
            history_service=history,
            output_registry=registry,
        )
        self.addCleanup(server.close)
        self.assertEqual(server.request("GET", f"/api/v1/outputs/{'e' * 32}")[0], 200)
        self.assertEqual(
            server.request("DELETE", f"/api/v1/history/{'e' * 32}")[0], 200
        )
        self.assertEqual(server.request("GET", f"/api/v1/outputs/{'e' * 32}")[0], 404)


class TestGenerateRoutePersistsHistory(unittest.TestCase):
    def test_generate_auto_records_history(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        history = HistoryService(Path(tmp.name) / "imagegen.db")

        class FakeEngine:
            def generate(self, request):
                return make_result(gid="c" * 32, path="out.png")

        svc = GenerationService(engine=FakeEngine(), history_service=history)
        server = ApiTestServer(
            config_path=Path(tmp.name) / "config.json",
            generation_service=svc,
            history_service=history,
        )
        self.addCleanup(server.close)
        status, _, data = server.json(
            "POST", "/api/v1/generate", {"prompt": "hello", "size": "1024x1024"}
        )
        self.assertEqual(status, 200)
        record = history.get("c" * 32)
        self.assertIsNotNone(record)
        self.assertEqual(record.prompt, "hello")
