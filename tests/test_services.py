"""Service 层测试（全部 mock，不依赖真实网络）。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from imagegen import (
    ConfigService,
    DiagnosticService,
    GenerationService,
    ModelService,
)
from imagegen.engine import ImageGenEngine
from imagegen.models import GenerateRequest, GenerateResult


class FakeEngine:
    def __init__(self):
        self.calls = []

    def generate(self, request):
        self.calls.append(request)
        return GenerateResult(
            path="out.png",
            backend="vertex",
            image_model_used="gemini-3-pro-image",
            seed=1,
            requested_size="1024x1024",
            actual_size="1024x1024",
            prompt_used="p",
        )


class TestGenerationService(unittest.TestCase):
    def test_generate_delegates_to_engine(self):
        fake = FakeEngine()
        svc = GenerationService(engine=fake)
        req = GenerateRequest(prompt="x")
        result = svc.generate(req)
        self.assertIsInstance(result, GenerateResult)
        self.assertIs(fake.calls[0], req)
        self.assertEqual(result.image_model_used, "gemini-3-pro-image")

    def test_edit_aliases_generate(self):
        fake = FakeEngine()
        svc = GenerationService(engine=fake)
        svc.edit(GenerateRequest(prompt="x"))
        self.assertEqual(len(fake.calls), 1)

    def test_default_engine_is_imagegen_engine(self):
        self.assertIsInstance(GenerationService()._engine, ImageGenEngine)


class TestModelService(unittest.TestCase):
    def test_list_backends_structured(self):
        items = ModelService().list_backends()
        ids = [item["id"] for item in items]
        self.assertIn("vertex", ids)
        self.assertIn("openai-compatible", ids)
        for item in items:
            self.assertIn("api_version", item)
            self.assertIn("capabilities", item)

    def test_get_backend_info_vertex_mocked(self):
        vertex_info = {
            "model": "gemini-3-pro-image",
            "base_url": "http://127.0.0.1:2156/v1",
            "image_models": ["gemini-3-pro-image", "gemini-2.5-flash-image"],
        }
        with (
            mock.patch("imagegen.backends.vertex.discover_vertex", return_value=vertex_info),
            mock.patch("imagegen.services.models.discover_vertex", return_value=vertex_info),
        ):
            info = ModelService().get_backend_info("vertex")
        self.assertEqual(info["best_model"], "gemini-3-pro-image")
        self.assertEqual(info["base_url"], "http://127.0.0.1:2156/v1")
        self.assertEqual(info["models"], vertex_info["image_models"])

    def test_list_models_vertex_mocked(self):
        with mock.patch(
            "imagegen.backends.vertex.discover_vertex",
            return_value={"model": "m", "base_url": "", "image_models": ["a", "b"]},
        ):
            self.assertEqual(ModelService().list_models("vertex"), ["a", "b"])


class TestConfigService(unittest.TestCase):
    def test_load_and_masked_delegate(self):
        with (
            mock.patch("imagegen.services.config.load_config", return_value={"a": 1}),
            mock.patch("imagegen.services.config.mask_config", return_value={"a": 1}),
        ):
            svc = ConfigService()
            self.assertEqual(svc.load(), {"a": 1})
            self.assertEqual(svc.masked(), {"a": 1})

    def test_load_raw_reads_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.json"
            cfg_path.write_text(json.dumps({"k": "v"}), encoding="utf-8")
            with mock.patch("imagegen.services.config.CONFIG_FILE", cfg_path):
                self.assertEqual(ConfigService().load_raw(), {"k": "v"})
            with mock.patch("imagegen.services.config.CONFIG_FILE", Path(tmp) / "missing.json"):
                self.assertEqual(ConfigService().load_raw(), {})

    def test_save_delegates(self):
        with mock.patch(
            "imagegen.services.config.save_config", return_value="/tmp/config.json"
        ) as save:
            result = ConfigService().save({"a": 1})
        save.assert_called_once_with({"a": 1})
        self.assertEqual(result, "/tmp/config.json")

    def test_path_and_exists(self):
        svc = ConfigService()
        self.assertTrue(str(svc.path()).endswith("config.json"))
        self.assertIsInstance(svc.exists(), bool)


class TestDiagnosticService(unittest.TestCase):
    def test_doctor_health_check_mocked(self):
        fake_http = mock.Mock(
            return_value=(200, b'{"data":[{"id":"gemini-3-pro-image"}]}', "application/json")
        )
        with (
            mock.patch("imagegen.services.diagnostics.load_config", return_value={}),
            mock.patch(
                "imagegen.services.diagnostics.discover_vertex",
                return_value={
                    "model": "gemini-3-pro-image",
                    "base_url": "http://127.0.0.1:2156/v1",
                    "api_key": "sk-test",
                },
            ),
            mock.patch("imagegen.services.diagnostics.http", fake_http),
        ):
            result = DiagnosticService().doctor()
        self.assertTrue(result["ok"])
        self.assertEqual(result["backend"], "vertex")
        self.assertEqual(result["checks"][0]["best_model"], "gemini-3-pro-image")
        self.assertEqual(result["checks"][0]["model_count"], 1)

    def test_size_probe_delegates(self):
        probe = {"ok": True, "backend": "vertex", "probes": []}
        with (
            mock.patch("imagegen.services.diagnostics.load_config", return_value={}),
            mock.patch(
                "imagegen.services.diagnostics.run_size_probe", return_value=probe
            ) as run,
        ):
            result = DiagnosticService().doctor(size_probe=True, size="1024x1024")
        run.assert_called_once()
        self.assertEqual(result["probes"], [])
