"""Phase 5B WebUI 前端契约测试：Reference Panel / Asset API / 素材库 / 拖放 / 粘贴。"""

from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
APP_JS = REPO_ROOT / "src" / "imagegen" / "web" / "static" / "app.js"
INDEX_HTML = REPO_ROOT / "src" / "imagegen" / "web" / "static" / "index.html"


class TestWebReferenceContract(unittest.TestCase):
    def setUp(self):
        self.js = APP_JS.read_text(encoding="utf-8")
        self.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_file_picker_multiple(self):
        self.assertIn('type="file"', self.html)
        self.assertIn("multiple", self.html)
        self.assertIn(
            'accept="image/png,image/jpeg,image/webp,image/gif"', self.html
        )
        self.assertIn("refFile", self.js)

    def test_asset_api_usage(self):
        for token in ("/api/v2/assets", "uploadAsset", "importAsset", "listAssets"):
            self.assertIn(token, self.js, token)

    def test_references_contract(self):
        self.assertIn("references: refs.map", self.js)
        self.assertIn("asset_id: r.asset_id", self.js)
        self.assertIn("role: r.role", self.js)
        self.assertNotIn("images: paths", self.js)

    def test_drag_and_drop(self):
        for token in ("dragenter", "dragover", "dragleave", '"drop"', "dataTransfer"):
            self.assertIn(token, self.js, token)

    def test_clipboard_paste(self):
        for token in ('"paste"', "clipboardData", "getAsFile"):
            self.assertIn(token, self.js, token)

    def test_asset_library(self):
        self.assertIn("素材库", self.html)
        for token in ("libGrid", "libSearch", "libAddBtn"):
            self.assertIn(token, self.html, token)

    def test_role_selector(self):
        self.assertIn("refrole", self.js)
        for role in (
            "character", "outfit", "style", "scene", "composition", "pose", "object",
        ):
            self.assertIn(role, self.js, role)

    def test_max_four_refs(self):
        self.assertIn("MAX_REFS = 4", self.js)
        self.assertIn("参考图最多 ", self.js)

    def test_generate_payload_has_no_managed_path(self):
        self.assertNotIn("file_path", self.js)
        self.assertNotIn("refPaths", self.js)
        self.assertNotIn("images: refs", self.js)

    def test_upload_status_states(self):
        for token in ('"queued"', '"uploading"', '"ready"', '"failed"'):
            self.assertIn(token, self.js, token)

    def test_remove_reference(self):
        self.assertIn("refdel", self.js)

    def test_local_path_import_is_advanced(self):
        self.assertIn("从服务器本机路径导入", self.html)
        self.assertIn("importPath", self.html)
        self.assertIn("importBtn", self.html)
        self.assertIn("importAsset", self.js)

    def test_no_manual_path_as_main_interaction(self):
        self.assertNotIn("参考图路径（本机路径", self.html)
        self.assertNotIn('textarea id="refPaths"', self.html)
        self.assertNotIn("ref_type", self.html)

    def test_count_indicator(self):
        self.assertIn('id="refCount"', self.html)
        self.assertIn("0 / 4", self.html)
