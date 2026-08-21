"""size_policy 契约测试：normalize / size_matches / validate / Engine 集成 / CLI。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from imagegen import engine as engine_mod
from imagegen.backends.base import BackendCapabilities
from imagegen.cli import build_parser
from imagegen.config import load_config
from imagegen.errors import BackendError, ValidationError
from imagegen.image_utils import size_matches
from imagegen.models import GenerateRequest, normalize_size_policy

from ._helpers import make_png_bytes


class SizedVertexBackend:
    """返回固定尺寸的 Vertex 后端替身。"""

    id = "vertex"

    def __init__(self, size=(1024, 1024)):
        self.size = size

    def capabilities(self):
        return BackendCapabilities(
            text_to_image=True, image_to_image=True, multi_reference=True
        )

    def resolve_model(self, cfg, requested=""):
        return (requested or "").strip() or "gemini-3-pro-image"

    def generate(self, cfg, prompt, width, height, model="", **kwargs):
        return make_png_bytes(*self.size)

    def generate_fallback_size(self, cfg, prompt, width, height, model="", **kwargs):
        return make_png_bytes(*self.size)

    def edit(self, cfg, prompt, width, height, model, images, **kwargs):
        return make_png_bytes(*self.size)


class TestNormalizeSizePolicy(unittest.TestCase):
    def test_empty_maps_to_auto(self):
        self.assertEqual(normalize_size_policy(""), ("auto", []))

    def test_formal_values(self):
        self.assertEqual(normalize_size_policy("auto"), ("auto", []))
        self.assertEqual(normalize_size_policy("aspect"), ("aspect", []))
        self.assertEqual(normalize_size_policy("exact"), ("exact", []))

    def test_strict_maps_to_aspect_with_warning(self):
        policy, warnings = normalize_size_policy("strict")
        self.assertEqual(policy, "aspect")
        self.assertTrue(any("strict" in w and "deprecated" in w for w in warnings))

    def test_warn_maps_to_auto_with_warning(self):
        policy, warnings = normalize_size_policy("warn")
        self.assertEqual(policy, "auto")
        self.assertTrue(any("warn" in w and "deprecated" in w for w in warnings))

    def test_invalid_values_rejected(self):
        for value in ("hard", "pixel", "ratio", "best", "foo"):
            with self.assertRaises(ValidationError):
                normalize_size_policy(value)


class TestSizeMatches(unittest.TestCase):
    def test_exact_pixel_equality(self):
        self.assertTrue(size_matches((1024, 1536), (1024, 1536), "exact"))
        self.assertFalse(size_matches((1024, 1536), (1536, 2304), "exact"))
        self.assertFalse(size_matches((1024, 1536), (1023, 1536), "exact"))

    def test_aspect_ratio_and_orientation(self):
        self.assertTrue(size_matches((1024, 1536), (1536, 2304), "aspect"))
        self.assertFalse(size_matches((1024, 1536), (1536, 1024), "aspect"))

    def test_auto_reuses_best_effort(self):
        self.assertTrue(size_matches((1024, 1536), (1536, 2304), "auto"))
        self.assertFalse(size_matches((1024, 1536), (1536, 1024), "auto"))

    def test_none_actual_fails(self):
        self.assertFalse(size_matches((1024, 1536), None, "exact"))
        self.assertFalse(size_matches((1024, 1536), None, "aspect"))


class TestRequestValidation(unittest.TestCase):
    def _req(self, **kwargs):
        defaults = {"prompt": "画一张图"}
        defaults.update(kwargs)
        return GenerateRequest(**defaults)

    def test_formal_and_deprecated_values_accepted(self):
        for value in ("", "auto", "aspect", "exact", "strict", "warn"):
            self._req(size_policy=value).validate()

    def test_invalid_values_rejected(self):
        for value in ("hard", "pixel", "ratio", "best", "foo"):
            with self.assertRaises(ValidationError):
                self._req(size_policy=value).validate()


class TestEngineSizePolicyIntegration(unittest.TestCase):
    def _run(self, request: GenerateRequest, backend: SizedVertexBackend):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = load_config()
            cfg["save_dir"] = str(Path(tmp) / "out")
            cfg["size_policy"] = {"mode": "auto", "retries": 2, "tolerance": 0.06}
            with (
                mock.patch.object(engine_mod, "load_config", return_value=cfg),
                mock.patch.object(engine_mod, "get_backend", return_value=backend),
            ):
                return engine_mod.generate(request)

    def test_exact_success(self):
        result = self._run(
            GenerateRequest(
                prompt="画一张图", width=1024, height=1024, seed=1,
                translator="off", size_policy="exact",
            ),
            SizedVertexBackend((1024, 1024)),
        )
        self.assertTrue(result.size_check["match"])
        self.assertEqual(result.actual_size, "1024x1024")

    def test_exact_mismatch_rejected(self):
        with self.assertRaises(BackendError):
            self._run(
                GenerateRequest(
                    prompt="画一张图", width=1024, height=1024, seed=1,
                    translator="off", size_policy="exact",
                ),
                SizedVertexBackend((1536, 2304)),
            )

    def test_aspect_scaled_accepted(self):
        result = self._run(
            GenerateRequest(
                prompt="画一张图", width=1024, height=1536, seed=1,
                translator="off", size_policy="aspect",
            ),
            SizedVertexBackend((1536, 2304)),
        )
        self.assertTrue(result.size_check["match"])

    def test_aspect_orientation_mismatch_rejected(self):
        with self.assertRaises(BackendError):
            self._run(
                GenerateRequest(
                    prompt="画一张图", width=1024, height=1536, seed=1,
                    translator="off", size_policy="aspect",
                ),
                SizedVertexBackend((1536, 1024)),
            )

    def test_strict_normalized_to_aspect_with_warning(self):
        result = self._run(
            GenerateRequest(
                prompt="画一张图", width=1024, height=1536, seed=1,
                translator="off", size_policy="strict",
            ),
            SizedVertexBackend((1536, 2304)),
        )
        self.assertTrue(result.size_check["match"])
        self.assertTrue(
            any("strict" in w and "deprecated" in w for w in result.warnings)
        )

    def test_old_config_strict_mode_accepted(self):
        request = GenerateRequest(
            prompt="画一张图", width=1024, height=1536, seed=1, translator="off"
        )
        with tempfile.TemporaryDirectory() as tmp:
            cfg = load_config()
            cfg["save_dir"] = str(Path(tmp) / "out")
            cfg["size_policy"] = {"mode": "strict", "retries": 2, "tolerance": 0.06}
            with (
                mock.patch.object(engine_mod, "load_config", return_value=cfg),
                mock.patch.object(
                    engine_mod, "get_backend", return_value=SizedVertexBackend((1536, 2304))
                ),
            ):
                result = engine_mod.generate(request)
        self.assertTrue(result.size_check["match"])
        self.assertTrue(
            any("strict" in w and "deprecated" in w for w in result.warnings)
        )

    def test_auto_mismatch_keeps_with_warning(self):
        result = self._run(
            GenerateRequest(
                prompt="画一张图", width=1024, height=1024, seed=1,
                translator="off", size_policy="auto",
            ),
            SizedVertexBackend((1408, 768)),
        )
        self.assertFalse(result.size_check["match"])
        self.assertTrue(
            any("尺寸未完全匹配" in w for w in result.warnings)
        )


class TestCliSizePolicy(unittest.TestCase):
    def test_cli_accepts_new_and_deprecated_values(self):
        for value in ("auto", "aspect", "exact", "strict", "warn"):
            args = build_parser().parse_args(["generate", "x", "--size-policy", value])
            self.assertEqual(args.size_policy, value)
