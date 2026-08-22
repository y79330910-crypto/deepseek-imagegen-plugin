"""Legacy removal 扫描：生产代码 / 配置 / CLI / README 不得残留旧身份标记。"""

from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

# 任务书 A 项扫描清单（排除 .git / build / dist / 虚拟环境 / __pycache__ / 测试文件）
FORBIDDEN_TOKENS = (
    ".deepseek-imagegen",
    "DEEPSEEK_",
    "deepseek-imagegen",
    "Codex Adapter",
    "Vertex Proxy",
    "BackendError",
    "backend_error",
)

_SKIP_DIRS = {".git", "build", "dist", "__pycache__", ".venv", "venv", ".tox", "tests"}
_TEXT_SUFFIXES = {".py", ".toml", ".md", ".yml", ".yaml", ".js", ".html", ".css", ".json"}


class TestLegacyRemoval(unittest.TestCase):
    def test_no_legacy_tokens_in_production_artifacts(self):
        targets: list[Path] = [
            REPO_ROOT / "src",
            REPO_ROOT / "pyproject.toml",
            REPO_ROOT / "README.md",
            REPO_ROOT / ".github",
        ]
        files: list[Path] = []
        for target in targets:
            if target.is_file():
                files.append(target)
            elif target.is_dir():
                files.extend(p for p in target.rglob("*") if p.is_file())
        self.assertTrue(files)
        scanned = 0
        for path in files:
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            if path.suffix.lower() not in _TEXT_SUFFIXES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            scanned += 1
            for token in FORBIDDEN_TOKENS:
                self.assertNotIn(token, text, f"{path} 残留 {token!r}")
        self.assertGreater(scanned, 20)


class TestSeedContractScan(unittest.TestCase):
    def test_no_seed_randomness_contract_in_src(self):
        targets: list[Path] = [
            REPO_ROOT / "src",
            REPO_ROOT / "README.md",
            REPO_ROOT / "pyproject.toml",
        ]
        files: list[Path] = []
        for target in targets:
            if target.is_file():
                files.append(target)
            elif target.is_dir():
                files.extend(p for p in target.rglob("*") if p.is_file())
        forbidden = ("--seed", "随机种子", "request.seed", "result.seed", "可复现")
        for path in files:
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            if path.suffix.lower() not in _TEXT_SUFFIXES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for token in forbidden:
                self.assertNotIn(token, text, f"{path} 残留 seed 契约描述 {token!r}")

    def test_readme_does_not_advertise_seed(self):
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("seed", readme)
