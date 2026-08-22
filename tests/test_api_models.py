"""HTTP API v1 POST /api/v1/models 测试（translator / image 双 target）。"""

from __future__ import annotations

import unittest

from imagegen.errors import ValidationError

from .api_test_utils import ApiTestServer, FakeModelService


class TestModelsRoute(unittest.TestCase):
    def test_models_translator(self):
        fake = FakeModelService(models=["deepseek-v4-flash", "other"])
        server = ApiTestServer(model_service=fake)
        self.addCleanup(server.close)
        status, _, data = server.json(
            "POST", "/api/v1/models", {"target": "translator"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["models"], ["deepseek-v4-flash", "other"])

    def test_models_image(self):
        fake = FakeModelService(models=["gemini-3-pro-image"])
        server = ApiTestServer(model_service=fake)
        self.addCleanup(server.close)
        status, _, data = server.json("POST", "/api/v1/models", {"target": "image"})
        self.assertEqual(status, 200)
        self.assertEqual(data["models"], ["gemini-3-pro-image"])

    def test_models_unknown_target_400(self):
        fake = FakeModelService(exc=ValidationError("未知 target"))
        server = ApiTestServer(model_service=fake)
        self.addCleanup(server.close)
        status, _, data = server.json("POST", "/api/v1/models", {"target": "vertex"})
        self.assertEqual(status, 400)
        self.assertEqual(data["error"]["type"], "validation_error")

    def test_models_missing_target_400(self):
        server = ApiTestServer(model_service=FakeModelService())
        self.addCleanup(server.close)
        status, _, data = server.json("POST", "/api/v1/models", {})
        self.assertEqual(status, 400)
        self.assertEqual(data["error"]["type"], "validation_error")

    def test_models_wrong_method_405(self):
        server = ApiTestServer(model_service=FakeModelService())
        self.addCleanup(server.close)
        status, _, data = server.json("GET", "/api/v1/models")
        self.assertEqual(status, 405)
        self.assertEqual(data["error"]["type"], "method_not_allowed")

    def test_backend_endpoints_removed(self):
        server = ApiTestServer(model_service=FakeModelService())
        self.addCleanup(server.close)
        for path in (
            "/api/v1/backends",
            "/api/v1/backends/vertex",
            "/api/v1/backends/vertex/models",
        ):
            status, _, data = server.json("GET", path)
            self.assertEqual(status, 404, path)
            self.assertEqual(data["error"]["type"], "not_found", path)
