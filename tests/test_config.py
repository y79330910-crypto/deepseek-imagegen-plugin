"""配置测试：合并/打码 + IMAGEGEN_* 环境变量优先级与解析规则。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from imagegen.config import load_config, mask_config
from imagegen.errors import ConfigurationError


ENV_KEYS = (
    "IMAGEGEN_TRANSLATOR_BASE_URL",
    "IMAGEGEN_TRANSLATOR_API_KEY",
    "IMAGEGEN_TRANSLATOR_MODEL",
    "IMAGEGEN_TRANSLATOR_OUTPUT_LANG",
    "IMAGEGEN_IMAGE_BASE_URL",
    "IMAGEGEN_IMAGE_API_KEY",
    "IMAGEGEN_IMAGE_MODEL",
    "IMAGEGEN_IMAGE_QUALITY",
    "IMAGEGEN_DEFAULT_SIZE",
    "IMAGEGEN_SAVE_DIR",
    "IMAGEGEN_MIRROR_DIR",
    "IMAGEGEN_SIZE_CHECK_ENABLED",
    "IMAGEGEN_SIZE_CHECK_TOLERANCE",
)


class TestMergeAndMask(unittest.TestCase):
    def test_merge_and_mask(self):
        cfg = load_config()
        self.assertNotIn("characters", cfg)
        self.assertIn("presets", cfg["composition"])
        safe = mask_config(cfg)
        text = json.dumps(safe, ensure_ascii=False)
        self.assertNotIn(str(cfg.get("image", {}).get("api_key") or "sk-NOT-SET"), text)
        self.assertIn("(未设置)", text)

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


class TestEnvPrecedence(unittest.TestCase):
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

    def _config_file(self, data: dict) -> Path:
        path = Path(self.tmp.name) / "config.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_env_undefined_file_wins_over_default(self):
        path = self._config_file(
            {"save_dir": "/from/file", "translator": {"model": "file-model"}}
        )
        cfg = load_config(path)
        self.assertEqual(cfg["save_dir"], "/from/file")
        self.assertEqual(cfg["translator"]["model"], "file-model")

    def test_env_overrides_file(self):
        path = self._config_file({"save_dir": "/from/file"})
        os.environ["IMAGEGEN_SAVE_DIR"] = "/from/env"
        cfg = load_config(path)
        self.assertEqual(cfg["save_dir"], "/from/env")

    def test_string_env_explicit_empty_overrides(self):
        path = self._config_file({"translator": {"model": "file-model"}})
        os.environ["IMAGEGEN_TRANSLATOR_MODEL"] = ""
        cfg = load_config(path)
        self.assertEqual(cfg["translator"]["model"], "")

    def test_all_string_env_keys_apply(self):
        values = {
            "IMAGEGEN_TRANSLATOR_BASE_URL": ("translator", "base_url", "https://t"),
            "IMAGEGEN_TRANSLATOR_API_KEY": ("translator", "api_key", "sk-t-env"),
            "IMAGEGEN_TRANSLATOR_MODEL": ("translator", "model", "tr-model"),
            "IMAGEGEN_TRANSLATOR_OUTPUT_LANG": ("translator", "output_lang", "en"),
            "IMAGEGEN_IMAGE_BASE_URL": ("image", "base_url", "https://i"),
            "IMAGEGEN_IMAGE_API_KEY": ("image", "api_key", "sk-i-env"),
            "IMAGEGEN_IMAGE_MODEL": ("image", "model", "img-model"),
            "IMAGEGEN_IMAGE_QUALITY": ("image", "quality", "high"),
            "IMAGEGEN_DEFAULT_SIZE": ("", "default_size", "1920x1080"),
            "IMAGEGEN_SAVE_DIR": ("", "save_dir", "/env/out"),
            "IMAGEGEN_MIRROR_DIR": ("", "mirror_dir", "/env/mirror"),
        }
        for name, (section, key, value) in values.items():
            os.environ[name] = value
        cfg = load_config()
        for name, (section, key, value) in values.items():
            node = cfg if not section else cfg[section]
            self.assertEqual(node[key], value, name)

    def test_bool_env_forms(self):
        for raw, expected in (
            ("true", True), ("TRUE", True), ("1", True), ("on", True), ("On", True),
            ("yes", True), ("false", False), ("0", False), ("off", False),
            ("OFF", False), ("no", False),
        ):
            os.environ["IMAGEGEN_SIZE_CHECK_ENABLED"] = raw
            self.assertIs(load_config()["size_check"]["enabled"], expected, raw)

    def test_bool_env_invalid_raises(self):
        os.environ["IMAGEGEN_SIZE_CHECK_ENABLED"] = "maybe"
        with self.assertRaises(ConfigurationError):
            load_config()

    def test_bool_env_empty_raises(self):
        os.environ["IMAGEGEN_SIZE_CHECK_ENABLED"] = ""
        with self.assertRaises(ConfigurationError):
            load_config()

    def test_tolerance_env_parses(self):
        os.environ["IMAGEGEN_SIZE_CHECK_TOLERANCE"] = "0.12"
        self.assertAlmostEqual(load_config()["size_check"]["tolerance"], 0.12)

    def test_tolerance_env_invalid_raises(self):
        for raw in ("abc", "-1", "0", "", "nan", "inf"):
            os.environ["IMAGEGEN_SIZE_CHECK_TOLERANCE"] = raw
            with self.assertRaises(ConfigurationError, msg=raw):
                load_config()
