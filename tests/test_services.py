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

    def test_config_injection_reaches_engine(self):
        cfg = {"save_dir": "/custom", "default_size": "1024x1024"}
        svc = GenerationService(config=cfg)
        self.assertIs(svc._engine._config, cfg)


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
            "size_policy": {"mode": "auto", "retries": 2, "tolerance": 0.06},
            "prompt_library": {"enabled": True},
            "extra_backends": {
                "dragtokens": {"model": "gpt-image-2", "api_key": "sk-extra-456"}
            },
        }
        self.config_file.write_text(json.dumps(initial), encoding="utf-8")

    def _svc(self) -> ConfigService:
        return ConfigService(self.config_file)

    def test_update_plain_field_and_load(self):
        self._svc().update({"save_dir": "/new/out"})
        self.assertEqual(self._svc().load()["save_dir"], "/new/out")

    def test_update_nested_deep_merge(self):
        self._svc().update({"translator": {"engine": "gemini"}})
        cfg = self._svc().load()
        self.assertEqual(cfg["translator"]["engine"], "gemini")
        self.assertEqual(
            cfg["translator"]["deepseek"]["api_key"], "sk-real-secret-123"
        )  # 未 patch 的嵌套字段保留

    def test_bool_int_float_conversion(self):
        self._svc().update(
            {
                "prompt_library": {"enabled": "false", "top_k": "30"},
                "size_policy": {"retries": "3", "tolerance": "0.1"},
            }
        )
        cfg = self._svc().load()
        self.assertIs(cfg["prompt_library"]["enabled"], False)
        self.assertEqual(cfg["prompt_library"]["top_k"], 30)
        self.assertEqual(cfg["size_policy"]["retries"], 3)
        self.assertAlmostEqual(cfg["size_policy"]["tolerance"], 0.1)

    def test_masked_secret_not_overwritten(self):
        masked = self._svc().masked()
        patch_secret = masked["translator"]["deepseek"]["api_key"]
        self.assertIn("*", patch_secret)
        self._svc().update({"translator": {"deepseek": {"api_key": patch_secret}}})
        cfg = self._svc().load()
        self.assertEqual(cfg["translator"]["deepseek"]["api_key"], "sk-real-secret-123")

    def test_new_real_secret_updates(self):
        self._svc().update(
            {"translator": {"deepseek": {"api_key": "sk-brand-new-789"}}}
        )
        cfg = self._svc().load()
        self.assertEqual(cfg["translator"]["deepseek"]["api_key"], "sk-brand-new-789")

    def test_unknown_field_kept(self):
        self._svc().update({"custom_field": {"nested": 1}})
        cfg = self._svc().load()
        self.assertEqual(cfg["custom_field"], {"nested": 1})

    def test_extra_backend_lists_and_secret_preserved(self):
        self._svc().update(
            {
                "extra_backends": {
                    "dragtokens": {"sizes": "1024x1024, 2048x2048", "models": "a, b"}
                }
            }
        )
        cfg = self._svc().load()
        eb = cfg["extra_backends"]["dragtokens"]
        self.assertEqual(eb["sizes"], ["1024x1024", "2048x2048"])
        self.assertEqual(eb["models"], ["a", "b"])
        self.assertEqual(eb["api_key"], "sk-extra-456")

    def test_update_returns_masked_config(self):
        result = self._svc().update(
            {"translator": {"deepseek": {"api_key": "sk-x1234567890"}}}
        )
        self.assertNotIn("sk-x1234567890", json.dumps(result))


class TestConfigPathInjection(unittest.TestCase):
    def test_default_path_uses_single_rule(self):
        from imagegen.config import default_config_path

        self.assertEqual(ConfigService().path(), default_config_path())
        self.assertEqual(str(ConfigService().path()).endswith("config.json"), True)

    def test_str_and_path_injection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path_obj = Path(tmp) / "config.json"
            svc_a = ConfigService(str(path_obj))
            svc_b = ConfigService(path_obj)
            self.assertEqual(svc_a.path(), path_obj)
            self.assertEqual(svc_b.path(), path_obj)

    def test_paths_are_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path_a = Path(tmp) / "config_a.json"
            path_b = Path(tmp) / "config_b.json"
            svc_a = ConfigService(path_a)
            svc_b = ConfigService(path_b)
            svc_a.update({"save_dir": "/a/out", "translator": {"engine": "gemini"}})
            svc_b.update({"save_dir": "/b/out", "translator": {"engine": "off"}})
            self.assertEqual(svc_a.load()["save_dir"], "/a/out")
            self.assertEqual(svc_a.load()["translator"]["engine"], "gemini")
            self.assertEqual(svc_b.load()["save_dir"], "/b/out")
            self.assertEqual(svc_b.load()["translator"]["engine"], "off")
            self.assertFalse(path_a == path_b)


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
