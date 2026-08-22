"""配置测试：合并/打码 + Phase 6 双 OpenAI-Compatible 旧配置迁移。"""

from __future__ import annotations

import json
import unittest

from imagegen.config import _migrate_config, load_config, mask_config


class TestConfigMigration(unittest.TestCase):
    def test_legacy_translator_deepseek_migrates(self):
        cfg = _migrate_config(
            {
                "translator": {
                    "engine": "deepseek",
                    "deepseek": {
                        "base_url": "https://api.deepseek.com",
                        "api_key": "sk-x",
                        "model": "deepseek-v4-flash",
                    },
                }
            }
        )
        tr = cfg["translator"]
        self.assertTrue(tr["enabled"])
        self.assertEqual(tr["base_url"], "https://api.deepseek.com")
        self.assertEqual(tr["api_key"], "sk-x")
        self.assertEqual(tr["model"], "deepseek-v4-flash")
        self.assertNotIn("engine", tr)
        self.assertNotIn("deepseek", tr)
        self.assertNotIn("gemini", tr)

    def test_legacy_translator_off_disables(self):
        cfg = _migrate_config({"translator": {"engine": "off"}})
        self.assertFalse(cfg["translator"]["enabled"])

    def test_vertex_and_extra_backends_not_guessed(self):
        cfg = _migrate_config(
            {
                "vertex": {
                    "base_url": "http://127.0.0.1:2156/v1",
                    "api_key": "sk-v",
                    "model": "gemini-3-pro-image",
                },
                "extra_backends": {
                    "dragtokens": {
                        "base_url": "https://x",
                        "api_key": "sk-d",
                        "model": "gpt-image-2",
                    }
                },
            }
        )
        self.assertEqual(cfg["image"].get("base_url") or "", "")
        self.assertEqual(cfg["image"].get("api_key") or "", "")
        self.assertNotIn("vertex", cfg)
        self.assertNotIn("extra_backends", cfg)

    def test_size_policy_tolerance_migrates(self):
        cfg = _migrate_config({"size_policy": {"tolerance": 0.12}})
        self.assertNotIn("size_policy", cfg)
        self.assertEqual(cfg["size_check"]["tolerance"], 0.12)

    def test_load_config_has_new_structure(self):
        cfg = load_config()
        self.assertIn("enabled", cfg["translator"])
        self.assertIn("base_url", cfg["translator"])
        self.assertIn("api_key", cfg["translator"])
        self.assertIn("model", cfg["translator"])
        self.assertIn("image", cfg)
        self.assertIn("base_url", cfg["image"])
        self.assertIn("quality", cfg["image"])
        self.assertIn("size_check", cfg)
        self.assertNotIn("vertex", cfg)
        self.assertNotIn("extra_backends", cfg)
        self.assertNotIn("size_policy", cfg)


class TestMergeAndMask(unittest.TestCase):
    def test_merge_and_mask(self):
        cfg = load_config()
        self.assertNotIn("characters", cfg)
        self.assertIn("presets", cfg["composition"])
        safe = mask_config(cfg)
        text = json.dumps(safe, ensure_ascii=False)
        self.assertNotIn(str(cfg.get("image", {}).get("api_key") or "sk-NOT-SET"), text)
        self.assertIn("(未设置)", text)
