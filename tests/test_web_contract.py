"""新 WebUI 前端契约测试：只用 /api/v2/*，Phase 6 双 OpenAI-Compatible UI。"""

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
            "/api/v2/generate",
            "/api/v2/config",
            "/api/v2/models",
            "/api/v2/doctor",
            "/api/v2/history",
            "/api/v2/assets",
        ):
            self.assertIn(endpoint, text, endpoint)

    def test_no_legacy_generation_chain(self):
        text = APP_JS.read_text(encoding="utf-8")
        for forbidden in (
            "/api/generate",
            "subprocess",
            "image_gen.py",
        ):
            self.assertNotIn(forbidden, text, forbidden)

    def test_no_backend_endpoints_in_js(self):
        text = APP_JS.read_text(encoding="utf-8")
        self.assertNotIn("/api/v2/backends", text)
        self.assertNotIn("listBackends", text)

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

    def test_dual_openai_api_settings(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn('data-path="translator.base_url"', html)
        self.assertIn('data-path="translator.api_key"', html)
        self.assertIn('data-path="translator.model"', html)
        self.assertIn('data-path="translator.enabled"', html)
        self.assertIn('data-path="image.base_url"', html)
        self.assertIn('data-path="image.api_key"', html)
        self.assertIn('data-path="image.model"', html)
        self.assertIn('data-path="image.quality"', html)
        self.assertIn('data-path="size_check.enabled"', html)

    def test_no_legacy_provider_ui(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        js = APP_JS.read_text(encoding="utf-8")
        for token in ("出图后端", "Vertex", "extra_backends", "备用后端", "DeepSeek", "Gemini"):
            self.assertNotIn(token, html, token)
            self.assertNotIn(token, js, token)

    def test_model_pull_and_manual_input(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        js = APP_JS.read_text(encoding="utf-8")
        self.assertIn("trPullBtn", html)
        self.assertIn("imgPullBtn", html)
        self.assertIn("trModelList", html)
        self.assertIn("imgCfgModelList", html)
        self.assertIn("imgModelList", html)
        self.assertIn("pullModels", js)
        self.assertIn("可手填", html)

    def test_aspect_and_tier_presets(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        js = APP_JS.read_text(encoding="utf-8")
        self.assertIn("sizeAspect", html)
        self.assertIn("sizeTier", html)
        for tier in ("1K", "2K", "4K"):
            self.assertIn(tier, html, tier)
        self.assertIn("SIZE_TABLE", js)
        self.assertIn('"16:9"', js)
        self.assertIn("applySizePreset", js)

    def test_quality_default_sends_nothing(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn("默认（不发送）", html)
        self.assertIn('value="low"', html)
        self.assertIn('value="medium"', html)
        self.assertIn('value="high"', html)

    def test_generate_page_has_no_backend_or_engine_select(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertNotIn('id="backend"', html)
        self.assertNotIn("翻译官引擎", html)
        self.assertIn("提示词处理", html)
