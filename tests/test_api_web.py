"""Standalone WebUI 静态服务测试（同源 / 与 /assets/*）。"""

from __future__ import annotations

import unittest

from .api_test_utils import ApiTestServer


class TestStaticServing(unittest.TestCase):
    def setUp(self):
        self.server = ApiTestServer()
        self.addCleanup(self.server.close)

    def test_index_html(self):
        status, headers, body = self.server.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertTrue(headers.get("Content-Type", "").startswith("text/html"))
        text = body.decode("utf-8")
        self.assertIn("<!DOCTYPE html>", text)
        self.assertIn('href="/assets/style.css"', text)
        self.assertIn('src="/assets/app.js"', text)

    def test_app_js(self):
        status, headers, body = self.server.request("GET", "/assets/app.js")
        self.assertEqual(status, 200)
        self.assertTrue(headers.get("Content-Type", "").startswith("application/javascript"))
        self.assertIn(b"/api/v1/generate", body)

    def test_style_css(self):
        status, headers, body = self.server.request("GET", "/assets/style.css")
        self.assertEqual(status, 200)
        self.assertTrue(headers.get("Content-Type", "").startswith("text/css"))
        self.assertIn(b"--cyan", body)

    def test_unknown_asset_404(self):
        status, _, data = self.server.json("GET", "/assets/nope.js")
        self.assertEqual(status, 404)
        self.assertEqual(data["error"]["type"], "not_found")

    def test_traversal_404(self):
        status, _, data = self.server.json(
            "GET", "/assets/..%2F..%2Fconfig.json"
        )
        self.assertEqual(status, 404)

    def test_spa_fallback_not_implemented(self):
        status, _, _ = self.server.json("GET", "/some/unknown/page")
        self.assertEqual(status, 404)

    def test_api_routes_still_work(self):
        status, _, data = self.server.json("GET", "/api/v1/health")
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "ok")
