"""Public Core API 验收测试。"""

from __future__ import annotations

import unittest

import imagegen
from imagegen import (
    CORE_API_VERSION,
    ConfigService,
    DiagnosticService,
    GenerateRequest,
    GenerateResult,
    GenerationService,
    ImageGenEngine,
    ImageGenError,
    ModelService,
)
from imagegen.errors import (
    ConfigurationError,
    EmptyImageError,
    HTTPStatusError,
    UpstreamError,
    ValidationError,
)


class TestPublicApi(unittest.TestCase):
    def test_core_api_version(self):
        self.assertEqual(CORE_API_VERSION, 2)

    def test_public_objects_accessible_from_root(self):
        for obj in (
            ImageGenEngine,
            GenerateRequest,
            GenerateResult,
            ImageGenError,
            GenerationService,
            ModelService,
            ConfigService,
            DiagnosticService,
        ):
            self.assertIsInstance(obj, type, obj)

    def test_all_exports_resolve(self):
        for name in imagegen.__all__:
            self.assertTrue(hasattr(imagegen, name), name)

    def test_error_hierarchy(self):
        self.assertTrue(issubclass(ConfigurationError, ImageGenError))
        self.assertTrue(issubclass(UpstreamError, ImageGenError))
        self.assertTrue(issubclass(HTTPStatusError, UpstreamError))
        self.assertTrue(issubclass(EmptyImageError, UpstreamError))
        self.assertTrue(issubclass(ValidationError, ImageGenError))
        http_error = HTTPStatusError(404, "not found")
        self.assertEqual(http_error.status, 404)

    def test_engine_interface(self):
        engine = ImageGenEngine()
        self.assertTrue(callable(engine.generate))
        request = GenerateRequest(prompt="")
        with self.assertRaises(ValidationError):
            engine.generate(request)

    def test_request_size_string_field(self):
        req = GenerateRequest(prompt="x", size="1024x1024")
        self.assertEqual(req.size, "1024x1024")
