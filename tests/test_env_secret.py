"""env secret 安全：IMAGEGEN_* API Key 不能被 WebUI 保存操作物化到 config.json。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from imagegen import ConfigService

from .api_test_utils import ApiTestServer


ENV_KEYS = ("IMAGEGEN_IMAGE_API_KEY", "IMAGEGEN_TRANSLATOR_API_KEY")


class _EnvIsolatedTestCase(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in ENV_KEYS if k in os.environ}
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def tearDown(self):
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        os.environ.update(self._saved)

    def _config_path(self) -> Path:
        return Path(self.tmp.name) / "config.json"


class TestEnvSecretService(_EnvIsolatedTestCase):
    def test_env_secret_masked_but_not_persisted(self):
        path = self._config_path()
        os.environ["IMAGEGEN_IMAGE_API_KEY"] = "sk-env-secret-1234567890"
        svc = ConfigService(path)
        masked = svc.masked()
        self.assertIn("*", masked["image"]["api_key"])
        self.assertNotIn("sk-env-secret-1234567890", json.dumps(masked))
        # 回传未修改的 masked 值 → raw config 不得出现 api_key
        svc.update({"image": {"api_key": masked["image"]["api_key"]}})
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotIn("api_key", raw.get("image", {}))
        self.assertNotIn("sk-env-secret-1234567890", json.dumps(raw))
        # env 仍然影响 effective config
        self.assertEqual(svc.load()["image"]["api_key"], "sk-env-secret-1234567890")

    def test_env_secret_does_not_overwrite_raw_secret(self):
        path = self._config_path()
        path.write_text(
            json.dumps({"image": {"api_key": "sk-raw-111"}}), encoding="utf-8"
        )
        os.environ["IMAGEGEN_IMAGE_API_KEY"] = "sk-env-secret-222222222222"
        svc = ConfigService(path)
        masked = svc.masked()
        svc.update({"image": {"api_key": masked["image"]["api_key"]}})
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(raw["image"]["api_key"], "sk-raw-111")
        # effective 仍由 env 覆盖
        self.assertEqual(svc.load()["image"]["api_key"], "sk-env-secret-222222222222")

    def test_new_real_secret_is_written_to_raw(self):
        path = self._config_path()
        svc = ConfigService(path)
        svc.update({"translator": {"api_key": "sk-brand-new-333"}})
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(raw["translator"]["api_key"], "sk-brand-new-333")
        self.assertEqual(svc.load()["translator"]["api_key"], "sk-brand-new-333")

    def test_translator_env_secret_never_materialized(self):
        path = self._config_path()
        os.environ["IMAGEGEN_TRANSLATOR_API_KEY"] = "sk-env-translator-444444"
        svc = ConfigService(path)
        masked = svc.masked()
        svc.update({"translator": {"api_key": masked["translator"]["api_key"]}})
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotIn("api_key", raw.get("translator", {}))
        self.assertEqual(
            svc.load()["translator"]["api_key"], "sk-env-translator-444444"
        )


class TestEnvSecretHttp(_EnvIsolatedTestCase):
    def test_webui_save_does_not_materialize_env_secret(self):
        os.environ["IMAGEGEN_TRANSLATOR_API_KEY"] = "sk-env-translator-555555"
        server = ApiTestServer(config_path=self._config_path())
        self.addCleanup(server.close)
        status, _, data = server.json("GET", "/api/v2/config")
        self.assertEqual(status, 200)
        masked = data["config"]["translator"]["api_key"]
        self.assertIn("*", masked)
        status, _, _ = server.json(
            "PATCH",
            "/api/v2/config",
            {
                "translator": {
                    "api_key": masked,
                    "output_lang": "en",
                }
            },
        )
        self.assertEqual(status, 200)
        raw = json.loads(self._config_path().read_text(encoding="utf-8"))
        self.assertNotIn("api_key", raw.get("translator", {}))
        self.assertNotIn("sk-env-translator-555555", json.dumps(raw))
        status, _, data = server.json("GET", "/api/v2/config")
        self.assertEqual(data["config"]["translator"]["output_lang"], "en")
        self.assertIn("*", data["config"]["translator"]["api_key"])
