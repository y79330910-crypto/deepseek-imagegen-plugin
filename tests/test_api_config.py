"""HTTP API v1 Config 路由测试（临时配置，不触碰真实用户配置）。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from imagegen import ConfigService

from .api_test_utils import ApiTestServer


INITIAL_CONFIG = {
    "save_dir": "/tmp/out",
    "translator": {
        "engine": "deepseek",
        "deepseek": {"api_key": "sk-real-secret-123", "model": "deepseek-v4-flash"},
    },
    "size_policy": {"mode": "auto", "retries": 2, "tolerance": 0.06},
}


class TestConfigRoutes(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self._tempdir())
        self.config_path = self.tmp / "config.json"
        self.server = ApiTestServer(
            config_path=self.config_path, initial_config=INITIAL_CONFIG
        )
        self.addCleanup(self.server.close)

    def _tempdir(self):
        import tempfile

        handle = tempfile.TemporaryDirectory()
        self.addCleanup(handle.cleanup)
        return handle.name

    def test_get_config_masks_secret(self):
        status, _, data = self.server.json("GET", "/api/v1/config")
        self.assertEqual(status, 200)
        masked = data["config"]
        self.assertIn("*", masked["translator"]["deepseek"]["api_key"])
        self.assertNotIn("sk-real-secret-123", json.dumps(masked))

    def test_patch_plain_and_nested(self):
        status, _, data = self.server.json(
            "PATCH",
            "/api/v1/config",
            {"save_dir": "/new/out", "translator": {"engine": "gemini"}},
        )
        self.assertEqual(status, 200)
        cfg = ConfigService(self.config_path).load()
        self.assertEqual(cfg["save_dir"], "/new/out")
        self.assertEqual(cfg["translator"]["engine"], "gemini")
        self.assertEqual(
            cfg["translator"]["deepseek"]["api_key"], "sk-real-secret-123"
        )

    def test_patch_masked_secret_preserved(self):
        masked = ConfigService(self.config_path).masked()
        patch_secret = masked["translator"]["deepseek"]["api_key"]
        status, _, _ = self.server.json(
            "PATCH",
            "/api/v1/config",
            {"translator": {"deepseek": {"api_key": patch_secret}}},
        )
        self.assertEqual(status, 200)
        cfg = ConfigService(self.config_path).load()
        self.assertEqual(cfg["translator"]["deepseek"]["api_key"], "sk-real-secret-123")

    def test_patch_new_secret_updates(self):
        status, _, _ = self.server.json(
            "PATCH",
            "/api/v1/config",
            {"translator": {"deepseek": {"api_key": "sk-brand-new-999"}}},
        )
        self.assertEqual(status, 200)
        cfg = ConfigService(self.config_path).load()
        self.assertEqual(cfg["translator"]["deepseek"]["api_key"], "sk-brand-new-999")

    def test_config_isolated_from_default(self):
        self.server.json("PATCH", "/api/v1/config", {"save_dir": "/isolated"})
        raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(raw["save_dir"], "/isolated")

    def test_put_and_post_config_rejected(self):
        status, _, data = self.server.json(
            "PUT", "/api/v1/config", {"save_dir": "/x"}
        )
        self.assertEqual(status, 405)
        self.assertEqual(data["error"]["type"], "method_not_allowed")
        status, _, _ = self.server.json("POST", "/api/v1/config", {"save_dir": "/x"})
        self.assertEqual(status, 405)
