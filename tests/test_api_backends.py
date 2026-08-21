"""HTTP API v1 Backend 路由测试（mock ModelService）。"""

from __future__ import annotations

import unittest

from .api_test_utils import ApiTestServer, FakeModelService


class TestBackendRoutes(unittest.TestCase):
    def setUp(self):
        self.server = ApiTestServer(
            model_service=FakeModelService(
                backends=[
                    {
                        "id": "vertex",
                        "api_version": 1,
                        "capabilities": {"text_to_image": True},
                    }
                ],
                models=["gemini-3-pro-image"],
                info={
                    "id": "vertex",
                    "api_version": 1,
                    "capabilities": {},
                    "models": ["gemini-3-pro-image"],
                    "best_model": "gemini-3-pro-image",
                    "base_url": "http://127.0.0.1:2156/v1",
                },
            )
        )
        self.addCleanup(self.server.close)

    def test_list_backends(self):
        status, _, data = self.server.json("GET", "/api/v1/backends")
        self.assertEqual(status, 200)
        self.assertEqual(data["backends"][0]["id"], "vertex")
        self.assertIn("capabilities", data["backends"][0])

    def test_backend_info(self):
        status, _, data = self.server.json("GET", "/api/v1/backends/vertex")
        self.assertEqual(status, 200)
        self.assertEqual(data["best_model"], "gemini-3-pro-image")
        self.assertEqual(data["base_url"], "http://127.0.0.1:2156/v1")

    def test_backend_models(self):
        status, _, data = self.server.json("GET", "/api/v1/backends/dragtokens/models")
        self.assertEqual(status, 200)
        self.assertEqual(data["backend"], "dragtokens")
        self.assertEqual(data["models"], ["gemini-3-pro-image"])

    def test_unknown_backend_404(self):
        status, _, data = self.server.json("GET", "/api/v1/backends/nope")
        self.assertEqual(status, 404)
        self.assertEqual(data["error"]["type"], "not_found")

    def test_backend_routes_wrong_method(self):
        status, _, _ = self.server.json("POST", "/api/v1/backends/vertex")
        self.assertEqual(status, 405)
