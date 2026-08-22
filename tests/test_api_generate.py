"""HTTP API v1 POST /generate 集成测试。"""

from __future__ import annotations

import unittest

from imagegen.errors import UpstreamError, ValidationError
from imagegen.models import GenerateResult

from .api_test_utils import ApiTestServer, FakeGenerationService


class TestGenerateRoute(unittest.TestCase):
    def setUp(self):
        self.result = GenerateResult(
            path=r"D:\tmp\out.png",
            image_model_used="gemini-3-pro-image",
            seed=7,
            requested_size="1024x1024",
            actual_size="1024x1024",
            prompt_used="a cat",
            warnings=["w1"],
        )

    def test_generate_success(self):
        fake = FakeGenerationService(result=self.result)
        server = ApiTestServer(generation_service=fake)
        self.addCleanup(server.close)
        status, _, data = server.json(
            "POST",
            "/api/v1/generate",
            {"prompt": "a cat", "size": "1024x1024"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["generation_id"], self.result.generation_id)
        self.assertEqual(data["output_url"], f"/api/v1/outputs/{self.result.generation_id}")
        self.assertEqual(data["image_model_used"], "gemini-3-pro-image")
        self.assertEqual(data["warnings"], ["w1"])
        self.assertEqual(fake.last_request.prompt, "a cat")
        self.assertEqual(fake.last_request.size, "1024x1024")

    def test_validation_error_400(self):
        fake = FakeGenerationService(exc=ValidationError("prompt must not be empty"))
        server = ApiTestServer(generation_service=fake)
        self.addCleanup(server.close)
        status, _, data = server.json(
            "POST", "/api/v1/generate", {"prompt": "x"}
        )
        self.assertEqual(status, 400)
        self.assertEqual(data["error"]["type"], "validation_error")
        self.assertEqual(data["error"]["message"], "prompt must not be empty")

    def test_upstream_error_502(self):
        fake = FakeGenerationService(exc=UpstreamError("upstream timeout"))
        server = ApiTestServer(generation_service=fake)
        self.addCleanup(server.close)
        status, _, data = server.json(
            "POST", "/api/v1/generate", {"prompt": "x"}
        )
        self.assertEqual(status, 502)
        self.assertEqual(data["error"]["type"], "upstream_error")

    def test_unknown_exception_500_without_leak(self):
        fake = FakeGenerationService(
            exc=RuntimeError("secret detail D:\\private\\trace.py")
        )
        server = ApiTestServer(generation_service=fake)
        self.addCleanup(server.close)
        status, _, data = server.json(
            "POST", "/api/v1/generate", {"prompt": "x"}
        )
        self.assertEqual(status, 500)
        self.assertEqual(data["error"]["type"], "internal_error")
        self.assertEqual(data["error"]["message"], "internal server error")
        self.assertNotIn("secret detail", str(data))
        self.assertNotIn("private", str(data))

    def test_empty_body_400(self):
        server = ApiTestServer()
        self.addCleanup(server.close)
        status, _, data = server.json("POST", "/api/v1/generate", body="")
        self.assertEqual(status, 400)
        self.assertEqual(data["error"]["type"], "invalid_json")
