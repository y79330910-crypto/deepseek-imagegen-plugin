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
from imagegen.errors import ConfigurationError, ValidationError
from imagegen.models import GenerateRequest, GenerateResult


class FakeEngine:
    def __init__(self):
        self.calls = []

    def generate(self, request):
        self.calls.append(request)
        return GenerateResult(
            path="out.png",
            backend="openai",
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

    def test_config_injection_reaches_engine(self):
        cfg = {"save_dir": "/custom", "default_size": "1024x1024"}
        svc = GenerationService(config=cfg)
        self.assertIs(svc._engine._config, cfg)


class TestModelService(unittest.TestCase):
    def test_list_models_translator_mocked(self):
        fake_client = mock.Mock()
        fake_client.list_models.return_value = ["a", "b"]
        with (
            mock.patch(
                "imagegen.services.models.load_config",
                return_value={"translator": {"base_url": "https://t", "api_key": "sk-t"}},
            ),
            mock.patch("imagegen.services.models.OpenAIClient", return_value=fake_client),
        ):
            models = ModelService().list_models("translator")
        self.assertEqual(models, ["a", "b"])
        fake_client.list_models.assert_called_once()

    def test_list_models_image_mocked(self):
        fake_client = mock.Mock()
        fake_client.list_models.return_value = ["img-a", "img-b"]
        with (
            mock.patch(
                "imagegen.services.models.load_config",
                return_value={"image": {"base_url": "https://i", "api_key": "sk-i"}},
            ),
            mock.patch("imagegen.services.models.OpenAIClient", return_value=fake_client),
        ):
            models = ModelService().list_models("image")
        self.assertEqual(models, ["img-a", "img-b"])

    def test_unknown_target_rejected(self):
        with self.assertRaises(ValidationError):
            ModelService().list_models("vertex")

    def test_missing_credentials_raise(self):
        with (
            mock.patch(
                "imagegen.services.models.load_config",
                return_value={"image": {"base_url": "", "api_key": ""}},
            ),
            self.assertRaises(ConfigurationError),
        ):
            ModelService().list_models("image")


class TestConfigService(unittest.TestCase):
    def test_load_and_masked_delegate(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.json"
            cfg_path.write_text(json.dumps({"a": 1}), encoding="utf-8")
            svc = ConfigService(cfg_path)
            self.assertEqual(svc.load()["a"], 1)
            self.assertEqual(svc.masked()["a"], 1)

    def test_load_raw_reads_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.json"
            cfg_path.write_text(json.dumps({"k": "v"}), encoding="utf-8")
            self.assertEqual(ConfigService(cfg_path).load_raw(), {"k": "v"})
            self.assertEqual(ConfigService(Path(tmp) / "missing.json").load_raw(), {})

    def test_save_delegates(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.json"
            result = ConfigService(cfg_path).save({"a": 1})
            self.assertEqual(result, str(cfg_path))
            self.assertTrue(cfg_path.exists())
            self.assertEqual(json.loads(cfg_path.read_text(encoding="utf-8")), {"a": 1})

    def test_path_and_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.json"
            svc = ConfigService(cfg_path)
            self.assertEqual(svc.path(), cfg_path)
            self.assertFalse(svc.exists())
            svc.save({"a": 1})
            self.assertTrue(svc.exists())


class TestConfigServiceUpdate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config_dir = Path(self.tmp.name)
        self.config_file = self.config_dir / "config.json"
        initial = {
            "save_dir": "/tmp/out",
            "translator": {
                "engine": "deepseek",
                "deepseek": {"api_key": "sk-real-secret-123", "model": "deepseek-v4-flash"},
            },
            "size_check": {"enabled": True, "tolerance": 0.06},
            "prompt_library": {"enabled": True},
            "image": {"api_key": "sk-image-456"},
        }
        self.config_file.write_text(json.dumps(initial), encoding="utf-8")

    def _svc(self) -> ConfigService:
        return ConfigService(self.config_file)

    def test_update_plain_field_and_load(self):
        self._svc().update({"save_dir": "/new/out"})
        self.assertEqual(self._svc().load()["save_dir"], "/new/out")

    def test_update_nested_deep_merge(self):
        self._svc().update({"translator": {"output_lang": "en"}})
        cfg = self._svc().load()
        self.assertEqual(cfg["translator"]["output_lang"], "en")
        self.assertEqual(cfg["translator"]["api_key"], "sk-real-secret-123")

    def test_bool_int_float_conversion(self):
        self._svc().update(
            {
                "prompt_library": {"enabled": "false", "top_k": "30"},
                "size_check": {"tolerance": "0.1"},
            }
        )
        cfg = self._svc().load()
        self.assertIs(cfg["prompt_library"]["enabled"], False)
        self.assertEqual(cfg["prompt_library"]["top_k"], 30)
        self.assertAlmostEqual(cfg["size_check"]["tolerance"], 0.1)

    def test_masked_secret_not_overwritten(self):
        masked = self._svc().masked()
        patch_secret = masked["translator"]["api_key"]
        self.assertIn("*", patch_secret)
        self._svc().update({"translator": {"api_key": patch_secret}})
        cfg = self._svc().load()
        self.assertEqual(cfg["translator"]["api_key"], "sk-real-secret-123")

    def test_new_real_secret_updates(self):
        self._svc().update({"translator": {"api_key": "sk-brand-new-789"}})
        cfg = self._svc().load()
        self.assertEqual(cfg["translator"]["api_key"], "sk-brand-new-789")

    def test_image_secret_preserved_and_updateable(self):
        masked = self._svc().masked()
        self._svc().update({"image": {"api_key": masked["image"]["api_key"]}})
        cfg = self._svc().load()
        self.assertEqual(cfg["image"]["api_key"], "sk-image-456")
        self._svc().update({"image": {"api_key": "sk-image-new"}})
        self.assertEqual(self._svc().load()["image"]["api_key"], "sk-image-new")

    def test_unknown_field_kept(self):
        self._svc().update({"custom_field": {"nested": 1}})
        cfg = self._svc().load()
        self.assertEqual(cfg["custom_field"], {"nested": 1})

    def test_update_returns_masked_config(self):
        result = self._svc().update({"translator": {"api_key": "sk-x1234567890"}})
        self.assertNotIn("sk-x1234567890", json.dumps(result))


class TestDiagnosticService(unittest.TestCase):
    def test_doctor_checks_both_targets_mocked(self):
        fake_client = mock.Mock()
        fake_client.list_models.return_value = ["a", "b"]
        with (
            mock.patch(
                "imagegen.services.diagnostics.load_config",
                return_value={
                    "translator": {"base_url": "https://t", "api_key": "sk-t"},
                    "image": {"base_url": "https://i", "api_key": "sk-i"},
                },
            ),
            mock.patch(
                "imagegen.services.diagnostics.OpenAIClient", return_value=fake_client
            ),
        ):
            result = DiagnosticService().doctor()
        self.assertTrue(result["ok"])
        self.assertEqual(result["backend"], "openai")
        self.assertEqual(len(result["checks"]), 2)
        for check in result["checks"]:
            self.assertTrue(check["ok"])
            self.assertEqual(check["model_count"], 2)

    def test_doctor_reports_missing_credentials(self):
        with (
            mock.patch(
                "imagegen.services.diagnostics.load_config",
                return_value={
                    "translator": {"base_url": "", "api_key": ""},
                    "image": {"base_url": "https://i", "api_key": ""},
                },
            ),
            mock.patch("imagegen.services.diagnostics.OpenAIClient"),
        ):
            result = DiagnosticService().doctor()
        self.assertFalse(result["ok"])
        self.assertEqual(len(result["checks"]), 2)
        self.assertFalse(result["checks"][0]["ok"])
        self.assertFalse(result["checks"][1]["ok"])
