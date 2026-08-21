"""Client boundary 测试：CLI / WebUI 不再直接依赖 Core 内部实现。"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WEBUI = REPO_ROOT / "plugins" / "deepseek-imagegen" / "scripts" / "webui.py"
CLI = REPO_ROOT / "src" / "imagegen" / "cli.py"

# 外围客户端禁止直接 import 的 Core 内部模块
FORBIDDEN_IMPORTS = (
    "imagegen.backends",
    ".backends",
    "imagegen.image_utils",
    ".image_utils",
    "imagegen.config",
    ".config",
    "imagegen.reference",
    ".reference",
    "imagegen.doctor",
    ".doctor",
)


def collect_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            prefix = "." * node.level if node.level else ""
            found.append(prefix + node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                found.append(alias.name)
    return found


class TestClientBoundary(unittest.TestCase):
    def test_webui_does_not_import_core_internals(self):
        imports = collect_imports(WEBUI)
        for mod in imports:
            for token in FORBIDDEN_IMPORTS:
                self.assertNotEqual(mod, token, f"webui.py 直接 import {token!r}")

    def test_cli_does_not_import_core_internals(self):
        imports = collect_imports(CLI)
        for mod in imports:
            for token in FORBIDDEN_IMPORTS:
                self.assertNotEqual(mod, token, f"cli.py 直接 import {token!r}")

    def test_cli_uses_services(self):
        imports = collect_imports(CLI)
        self.assertTrue(
            any(mod == ".services" or mod.startswith(".services.") for mod in imports),
            "cli.py 应通过 services 消费业务",
        )

    def test_webui_uses_public_api(self):
        imports = collect_imports(WEBUI)
        self.assertTrue(
            any(mod == "imagegen" or mod.startswith("imagegen.") for mod in imports),
            "webui.py 应通过 imagegen 公共入口消费 Core",
        )
