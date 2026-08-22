"""HTTP API v2 基础集成测试：server / health / 路由 / 错误契约 / remote bind guard。"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest

import imagegen
from imagegen.api import HTTP_API_VERSION
from imagegen.api.server import validate_bind_address
from imagegen.errors import ConfigurationError

from .api_test_utils import ApiTestServer


class TestServerBasics(unittest.TestCase):
    def setUp(self):
        self.server = ApiTestServer()
        self.addCleanup(self.server.close)

    def test_health(self):
        status, _, data = self.server.json("GET", "/api/v2/health")
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["app_version"], imagegen.__version__)
        self.assertEqual(data["api_version"], HTTP_API_VERSION)
        self.assertEqual(data["core_api_version"], 2)

    def test_unknown_route_404(self):
        status, _, data = self.server.json("GET", "/api/v2/nope")
        self.assertEqual(status, 404)
        self.assertEqual(data["error"]["type"], "not_found")

    def test_unversioned_routes_404(self):
        status, _, _ = self.server.json("GET", "/health")
        self.assertEqual(status, 404)
        status, _, _ = self.server.json("GET", "/generate")
        self.assertEqual(status, 404)

    def test_v1_routes_404(self):
        for path in (
            "/api/v1/health",
            "/api/v1/generate",
            "/api/v1/config",
            "/api/v1/history",
            "/api/v1/assets",
            "/api/v1/outputs/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        ):
            status, _, _ = self.server.json("GET", path)
            self.assertEqual(status, 404, path)

    def test_wrong_method_405(self):
        status, headers, data = self.server.json("POST", "/api/v2/health")
        self.assertEqual(status, 405)
        self.assertEqual(data["error"]["type"], "method_not_allowed")
        self.assertIn("GET", headers.get("Allow", ""))

    def test_invalid_json_400(self):
        status, _, data = self.server.json("POST", "/api/v2/generate", body="{bad json")
        self.assertEqual(status, 400)
        self.assertEqual(data["error"]["type"], "invalid_json")

    def test_payload_too_large_400(self):
        big = "x" * (1024 * 1024 + 1)
        status, _, data = self.server.json("POST", "/api/v2/generate", body=big)
        self.assertEqual(status, 400)
        self.assertEqual(data["error"]["type"], "payload_too_large")


class TestRemoteBindGuard(unittest.TestCase):
    def test_loopback_allowed(self):
        self.assertEqual(validate_bind_address("127.0.0.1"), "127.0.0.1")
        self.assertEqual(validate_bind_address("localhost"), "localhost")
        self.assertEqual(validate_bind_address("::1"), "::1")
        self.assertEqual(validate_bind_address(""), "127.0.0.1")

    def test_remote_requires_allow_remote(self):
        with self.assertRaises(ConfigurationError):
            validate_bind_address("0.0.0.0")
        with self.assertRaises(ConfigurationError):
            validate_bind_address("192.168.1.5", allow_remote=False)

    def test_remote_allowed_with_flag(self):
        self.assertEqual(validate_bind_address("0.0.0.0", allow_remote=True), "0.0.0.0")


class TestServeHelp(unittest.TestCase):
    def test_module_serve_help(self):
        env = dict(os.environ)
        from pathlib import Path

        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        proc = subprocess.run(
            [sys.executable, "-m", "imagegen", "serve", "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("--allow-remote", proc.stdout)
        self.assertIn("8765", proc.stdout)
