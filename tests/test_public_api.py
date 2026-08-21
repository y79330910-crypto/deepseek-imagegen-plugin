"""Public Core API 验收测试。"""

from __future__ import annotations

import unittest

import imagegen
from imagegen import (
    BackendCapabilities,
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
    BackendError,
    ConfigurationError,
    EmptyImageError,
    GenError,
    ValidationError,
)


class TestPublicApi(unittest.TestCase):
    def test_core_api_version(self):
        self.assertEqual(CORE_API_VERSION, 1)

    def test_public_objects_accessible_from_root(self):
        for obj in (
            ImageGenEngine,
            GenerateRequest,
            GenerateResult,
            BackendCapabilities,
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
        self.assertIs(GenError, ImageGenError)
        self.assertTrue(issubclass(ConfigurationError, ImageGenError))
        self.assertTrue(issubclass(BackendError, ImageGenError))
        self.assertTrue(issubclass(ValidationError, ImageGenError))
        self.assertTrue(issubclass(EmptyImageError, BackendError))

    def test_engine_interface(self):
        engine = ImageGenEngine()
        self.assertTrue(callable(engine.generate))
        request = GenerateRequest(prompt="")
        with self.assertRaises(ValidationError):
            engine.generate(request)

    def test_request_size_string_field(self):
        req = GenerateRequest(prompt="x", size="1024x1024")
        self.assertEqual(req.size, "1024x1024")
