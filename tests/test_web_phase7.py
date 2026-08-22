"""Phase 7 WebUI 前端契约测试：缩略图、懒加载、分页、参数复用。"""

from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
APP_JS = REPO_ROOT / "src" / "imagegen" / "web" / "static" / "app.js"
INDEX_HTML = REPO_ROOT / "src" / "imagegen" / "web" / "static" / "index.html"


class TestWebPhase7(unittest.TestCase):
    def setUp(self):
        self.js = APP_JS.read_text(encoding="utf-8")
        self.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_gallery_uses_thumbnail(self):
        self.assertIn("it.thumbnail_url || it.output_url", self.js)
        self.assertIn("data-fallback=", self.js)

    def test_asset_library_uses_thumbnail(self):
        self.assertIn("it.thumbnail_url || it.content_url", self.js)

    def test_lazy_loading_and_async_decoding(self):
        self.assertIn('loading="lazy"', self.js)
        self.assertIn('decoding="async"', self.js)

    def test_gallery_load_more(self):
        self.assertIn("GALLERY_PAGE_SIZE = 24", self.js)
        self.assertIn('id="galMoreBtn"', self.html)
        self.assertIn("galMoreBtn", self.js)
        self.assertIn('has_more', self.js)
        self.assertIn("next_offset", self.js)

    def test_asset_library_load_more(self):
        self.assertIn("LIB_PAGE_SIZE = 24", self.js)
        self.assertIn('id="libMoreBtn"', self.html)
        self.assertIn("libMoreBtn", self.js)

    def test_search_resets_pagination(self):
        self.assertIn('loadHistory($("galSearch").value.trim(), true)', self.js)
        self.assertIn('loadLibrary($("libSearch").value.trim(), true)', self.js)

    def test_history_detail_fetch_and_reuse(self):
        self.assertIn("getHistory", self.js)
        self.assertIn('"/api/v2/history/"', self.js)
        self.assertIn("reuseGeneration", self.js)
        self.assertIn('data-act="reuse"', self.js)
        self.assertIn("复用参数", self.js)

    def test_seed_removed_from_webui(self):
        self.assertNotIn("seed", self.js)
        self.assertNotIn("seed", self.html)
