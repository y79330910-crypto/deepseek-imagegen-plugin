"""ConfigService 显式配置路径注入回归测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from imagegen import ConfigService
from imagegen.config import default_config_path


class TestConfigPathInjection(unittest.TestCase):
    def test_default_path_uses_single_rule(self):
        self.assertEqual(ConfigService().path(), default_config_path())
        self.assertEqual(ConfigService().path().name, "config.json")

    def test_str_and_path_injection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path_obj = Path(tmp) / "config.json"
            self.assertEqual(ConfigService(str(path_obj)).path(), path_obj)
            self.assertEqual(ConfigService(path_obj).path(), path_obj)

    def test_lifecycle_uses_instance_path_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "custom.json"
            svc = ConfigService(cfg_path)
            self.assertFalse(svc.exists())
            svc.save({"save_dir": "/x", "translator": {"enabled": True}})
            self.assertTrue(svc.exists())
            self.assertTrue(svc.load()["translator"]["enabled"])
            self.assertEqual(svc.path(), cfg_path)
            result = svc.update({"save_dir": "/y", "size_check": {"tolerance": "0.08"}})
            self.assertEqual(result["save_dir"], "/y")
            self.assertEqual(svc.load()["size_check"]["tolerance"], 0.08)
            self.assertEqual(svc.masked()["save_dir"], "/y")
            # 实例路径与默认路径隔离
            self.assertNotEqual(svc.path(), default_config_path())

    def test_paths_are_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path_a = Path(tmp) / "config_a.json"
            path_b = Path(tmp) / "config_b.json"
            svc_a = ConfigService(path_a)
            svc_b = ConfigService(path_b)
            svc_a.update({"save_dir": "/a/out", "translator": {"enabled": True}})
            svc_b.update({"save_dir": "/b/out", "translator": {"enabled": False}})
            self.assertEqual(svc_a.load()["save_dir"], "/a/out")
            self.assertTrue(svc_a.load()["translator"]["enabled"])
            self.assertEqual(svc_b.load()["save_dir"], "/b/out")
            self.assertFalse(svc_b.load()["translator"]["enabled"])

    def test_load_raw_on_custom_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.json"
            cfg_path.write_text(json.dumps({"k": "v"}), encoding="utf-8")
            self.assertEqual(ConfigService(cfg_path).load_raw(), {"k": "v"})
