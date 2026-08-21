"""WebUI 静态资源必须来自 installed package（importlib.resources），不依赖仓库路径。"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from importlib.resources import files
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"


class TestPackageResources(unittest.TestCase):
    def test_resources_readable_via_importlib(self):
        static = files("imagegen.web").joinpath("static")
        for name in ("index.html", "app.js", "style.css"):
            target = static.joinpath(name)
            self.assertTrue(target.is_file(), name)
            self.assertGreater(target.stat().st_size, 0)

    def test_read_index_and_asset_helpers(self):
        from imagegen.web import asset_content_type, read_asset, read_index_html

        self.assertIn("<!DOCTYPE html>", read_index_html())
        self.assertIn(b"/api/v1/generate", read_asset("app.js"))
        self.assertTrue(asset_content_type("app.js").startswith("application/javascript"))

    def test_resources_work_from_non_source_cwd(self):
        """在非源码 cwd 下仍能找到资源（模拟 pip install 后运行）。"""
        env = dict(os.environ)
        env["PYTHONPATH"] = str(SRC_DIR)
        code = (
            "from importlib.resources import files;"
            "r = files('imagegen.web').joinpath('static');"
            "assert r.joinpath('index.html').is_file();"
            "assert r.joinpath('app.js').is_file();"
            "assert r.joinpath('style.css').is_file();"
            "print('resources ok')"
        )
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [sys.executable, "-c", code],
                cwd=tmp,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("resources ok", proc.stdout)
