"""验收测试：Core 可以在没有 Codex 插件结构的情况下独立导入与运行。"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"

# Core 中禁止出现的 Codex 耦合标记
FORBIDDEN_TOKENS = (
    ".codex-plugin",
    "SKILL.md",
    "plugins" + os.sep + "deepseek-imagegen",
    ".codex" + os.sep,
    "Documents" + os.sep + "Codex",
    "Pictures" + os.sep + "codex",
)


class TestCoreStandalone(unittest.TestCase):
    def test_core_importable_without_plugin_dirs(self):
        """在只有 src 的 PYTHONPATH 下导入 Core，证明不依赖 Codex 插件目录。"""
        env = dict(os.environ)
        env["PYTHONPATH"] = str(SRC_DIR)
        code = (
            "import imagegen, imagegen.engine, imagegen.cli, imagegen.doctor; "
            "print(imagegen.__file__); print(imagegen.CORE_API_VERSION)"
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
        out_lines = [line.strip() for line in proc.stdout.strip().splitlines() if line.strip()]
        self.assertTrue(out_lines, proc.stdout)
        core_file = out_lines[0]
        self.assertIn(os.sep + "src" + os.sep + "imagegen", core_file)
        self.assertNotIn("plugins" + os.sep + "deepseek-imagegen", core_file)
        self.assertNotIn("codex", core_file.lower())
        self.assertEqual(out_lines[1], "2")

    def test_cli_parser_builds_without_plugin_dirs(self):
        """CLI 参数解析不依赖插件目录。"""
        env = dict(os.environ)
        env["PYTHONPATH"] = str(SRC_DIR)
        code = (
            "from imagegen.cli import build_parser; "
            "a = build_parser().parse_args(['generate', 'x', '--translator', 'off', '--json']); "
            "print(a.prompt, a.translator)"
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
        self.assertEqual(proc.stdout.strip(), "x off")

    def test_no_codex_coupling_in_core(self):
        """src/imagegen 源码中不得出现 Codex 插件/安装路径等耦合标记。"""
        py_files = sorted(SRC_DIR.rglob("*.py"))
        self.assertTrue(py_files, "src/imagegen 下应有 Python 文件")
        for path in py_files:
            text = path.read_text(encoding="utf-8", errors="replace")
            for token in FORBIDDEN_TOKENS:
                self.assertNotIn(token, text, f"{path} 包含 Codex 耦合标记 {token!r}")
