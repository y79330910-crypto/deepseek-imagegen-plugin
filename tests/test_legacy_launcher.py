"""旧插件 WebUI launcher 兼容测试。"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
WEBUI_PY = REPO_ROOT / "plugins" / "deepseek-imagegen" / "scripts" / "webui.py"


class FakeServer:
    def __init__(self):
        self.closed = False

    def serve_forever(self):
        raise KeyboardInterrupt

    def server_close(self):
        self.closed = True


class TestLegacyLauncherContent(unittest.TestCase):
    def test_launcher_has_no_old_webui_implementation(self):
        text = WEBUI_PY.read_text(encoding="utf-8")
        for forbidden in (
            "PAGE_HTML",
            "<style>",
            "<script>",
            "subprocess",
            "BaseHTTPRequestHandler",
            "run_generate",
        ):
            self.assertNotIn(forbidden, text, forbidden)

    def test_launcher_delegates_to_standalone_server(self):
        text = WEBUI_PY.read_text(encoding="utf-8")
        self.assertIn("imagegen.api", text)
        self.assertIn("create_server", text)
        self.assertIn("Legacy plugin WebUI launcher is deprecated", text)
        self.assertIn("standalone ImageGen WebUI", text)

    def test_launcher_starts_standalone_server(self):
        spec = importlib.util.spec_from_file_location("legacy_webui_test", WEBUI_PY)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with (
            mock.patch("imagegen.api.create_server", return_value=FakeServer()),
            mock.patch("imagegen.cli.configure_console_utf8"),
        ):
            code = module.main(["--no-browser", "--port", "8765"])
        self.assertEqual(code, 0)
