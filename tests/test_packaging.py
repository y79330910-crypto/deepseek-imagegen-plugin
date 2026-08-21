"""独立打包 / 模块入口测试。"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"


class TestPackaging(unittest.TestCase):
    def test_module_entrypoint_help(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(SRC_DIR)
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [sys.executable, "-m", "imagegen", "--help"],
                cwd=tmp,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("usage: imagegen", proc.stdout)

    def test_pyproject_has_console_script(self):
        text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("[project.scripts]", text)
        self.assertIn('imagegen = "imagegen.cli:main"', text)
        self.assertIn('where = ["src"]', text)
