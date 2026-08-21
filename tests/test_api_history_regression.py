"""历史持久化 + 重启回归：同一 DB 在新进程（新 registry）下仍可取图/列历史。"""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from imagegen.api import OutputRegistry
from imagegen.models import GenerateRequest, GenerateResult
from imagegen.services.history import HistoryService

from .api_test_utils import ApiTestServer


def make_png_bytes() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (12, 12), (9, 8, 7)).save(buf, format="PNG")
    return buf.getvalue()


class TestRestartRegression(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workdir = Path(self.tmp.name)
        self.db = self.workdir / "imagegen.db"
        self.out_file = self.workdir / "result.png"
        self.out_file.write_bytes(make_png_bytes())

    def _seed(self) -> HistoryService:
        svc = HistoryService(self.db)
        svc.record(
            GenerateRequest(prompt="persisted prompt"),
            GenerateResult(
                path=str(self.out_file),
                backend="vertex",
                image_model_used="gemini-3-pro-image",
                seed=3,
                requested_size="1024x1024",
                actual_size="1024x1024",
                prompt_used="rewritten",
                generation_id="a" * 32,
            ),
        )
        return svc

    def test_restart_keeps_history_and_output(self):
        self._seed()
        # 模拟第一次运行：写历史、注册输出
        server1 = ApiTestServer(
            config_path=self.workdir / "config.json",
            history_service=HistoryService(self.db),
            output_registry=OutputRegistry(),
        )
        server1.close()
        # 模拟重启：新 HistoryService、空 OutputRegistry（默认），同一 DB 文件
        server2 = ApiTestServer(
            config_path=self.workdir / "config.json",
            history_service=HistoryService(self.db),
        )
        self.addCleanup(server2.close)
        status, _, hist = server2.json("GET", "/api/v1/history")
        self.assertEqual(status, 200)
        self.assertEqual(hist["count"], 1)
        self.assertEqual(hist["items"][0]["prompt"], "persisted prompt")
        self.assertEqual(
            hist["items"][0]["output_url"], f"/api/v1/outputs/{'a' * 32}"
        )
        status, headers, body = server2.request("GET", f"/api/v1/outputs/{'a' * 32}")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "image/png")
        self.assertEqual(body, self.out_file.read_bytes())
