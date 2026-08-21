"""新 WebUI 前端契约测试：只用 /api/v1/*，不再出现旧生成链。"""

from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
APP_JS = REPO_ROOT / "src" / "imagegen" / "web" / "static" / "app.js"
INDEX_HTML = REPO_ROOT / "src" / "imagegen" / "web" / "static" / "index.html"


class TestWebContract(unittest.TestCase):
    def test_app_js_uses_v1_api_only(self):
        text = APP_JS.read_text(encoding="utf-8")
        for endpoint in (
            "/api/v1/generate",
            "/api/v1/config",
            "/api/v1/backends",
            "/api/v1/doctor",
            "/api/v1/history",
        ):
            self.assertIn(endpoint, text, endpoint)

    def test_no_legacy_generation_chain(self):
        text = APP_JS.read_text(encoding="utf-8")
        for forbidden in (
            "/api/generate",
            "/api/models",
            "subprocess",
            "image_gen.py",
        ):
            self.assertNotIn(forbidden, text, forbidden)

    def test_no_absolute_server_url_in_js(self):
        text = APP_JS.read_text(encoding="utf-8")
        self.assertNotIn("http://127.0.0.1", text)
        self.assertNotIn("localhost", text)

    def test_index_references_assets(self):
        text = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn('/assets/style.css', text)
        self.assertIn('/assets/app.js', text)

    def test_persistent_gallery(self):
        js = APP_JS.read_text(encoding="utf-8")
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn("listHistory", js)
        self.assertIn("deleteHistory", js)
        self.assertNotIn("sessionResults", js)
        self.assertIn("历史画廊", html)

    def test_no_denoise_control_in_ui(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        js = APP_JS.read_text(encoding="utf-8")
        self.assertNotIn("denoise", html)
        self.assertNotIn("denoise", js)

    def test_size_policy_only_formal_values(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn('value="aspect"', html)
        self.assertIn('value="exact"', html)
        self.assertNotIn('value="strict"', html)
        self.assertNotIn('value="warn"', html)
