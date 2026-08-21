"""配置测试：合并/打码 + translator.enabled 兼容迁移。"""

from __future__ import annotations

import json
import unittest

from imagegen.config import _migrate_translator, load_config, mask_config


class TestTranslatorMigration(unittest.TestCase):
    def test_enabled_false_maps_to_engine_off(self):
        cfg = _migrate_translator({"translator": {"enabled": False, "engine": "deepseek"}})
        tr = cfg["translator"]
        self.assertEqual(tr["engine"], "off")
        self.assertNotIn("enabled", tr)

    def test_enabled_true_without_engine_defaults_deepseek(self):
        cfg = _migrate_translator({"translator": {"enabled": True}})
        tr = cfg["translator"]
        self.assertEqual(tr["engine"], "deepseek")
        self.assertNotIn("enabled", tr)

    def test_enabled_true_keeps_explicit_engine(self):
        cfg = _migrate_translator({"translator": {"enabled": True, "engine": "gemini"}})
        tr = cfg["translator"]
        self.assertEqual(tr["engine"], "gemini")
        self.assertNotIn("enabled", tr)

    def test_engine_only_unchanged(self):
        cfg = _migrate_translator({"translator": {"engine": "off"}})
        self.assertEqual(cfg["translator"], {"engine": "off"})

    def test_load_config_has_no_enabled(self):
        cfg = load_config()
        self.assertNotIn("enabled", cfg["translator"])
        self.assertIn("engine", cfg["translator"])


class TestMergeAndMask(unittest.TestCase):
    def test_merge_and_mask(self):
        cfg = load_config()
        self.assertNotIn("characters", cfg)
        self.assertIn("presets", cfg["composition"])
        safe = mask_config(cfg)
        text = json.dumps(safe, ensure_ascii=False)
        self.assertNotIn(cfg.get("vertex", {}).get("api_key") or "sk-NOT-SET", text)
        self.assertIn("(未设置)", text)
