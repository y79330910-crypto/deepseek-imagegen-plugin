"""旧 ~/.deepseek-imagegen 完全忽略：不读取、不迁移、不影响运行时。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from imagegen import config as config_mod


class TestOldPathIgnored(unittest.TestCase):
    def test_default_paths_use_imagegen_only(self):
        self.assertEqual(
            config_mod.default_config_path().parent.name, ".imagegen"
        )
        self.assertEqual(
            config_mod.default_history_db_path().parent.name, ".imagegen"
        )
        self.assertEqual(
            config_mod.default_asset_dir().parent.parent.name, ".imagegen"
        )

    def test_old_config_never_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            old_dir = home / ".deepseek-imagegen"
            old_dir.mkdir()
            (old_dir / "config.json").write_text(
                json.dumps(
                    {
                        "translator": {
                            "api_key": "sk-old-secret",
                            "model": "old-model",
                            "base_url": "https://old.example.com/v1",
                        }
                    }
                ),
                encoding="utf-8",
            )
            new_dir = home / ".imagegen"
            new_dir.mkdir()
            (new_dir / "config.json").write_text(
                json.dumps({"translator": {"model": "new-model"}}),
                encoding="utf-8",
            )
            with mock.patch.object(Path, "home", return_value=home):
                cfg = config_mod.load_config()
            self.assertEqual(cfg["translator"]["model"], "new-model")
            self.assertEqual(cfg["translator"]["api_key"], "")
            self.assertEqual(cfg["translator"]["base_url"], "")
            self.assertNotEqual(cfg["translator"]["api_key"], "sk-old-secret")
