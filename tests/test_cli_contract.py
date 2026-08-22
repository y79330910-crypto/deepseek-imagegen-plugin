"""CLI / Core 2.0 contract：help 无历史架构词、旧请求字段严格拒绝。"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

from imagegen.errors import ValidationError
from imagegen.models import GenerateRequest

from .api_test_utils import ApiTestServer


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"

FORBIDDEN_CLI_TOKENS = (
    "--ref-type",
    "--denoise",
    "deepseek",
    "gemini",
    "backend",
    "codex",
    "vertex",
)


class TestCliHelpClean(unittest.TestCase):
    def test_all_help_commands_clean(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(SRC_DIR)
        commands = (
            ["--help"],
            ["generate", "--help"],
            ["translate", "--help"],
            ["serve", "--help"],
            ["doctor", "--help"],
            ["list-models", "--help"],
        )
        for cmd in commands:
            with self.subTest(cmd=cmd):
                proc = subprocess.run(
                    [sys.executable, "-m", "imagegen", *cmd],
                    cwd=REPO_ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=60,
                )
                self.assertEqual(proc.returncode, 0, (cmd, proc.stderr))
                text = (proc.stdout + proc.stderr).lower()
                for token in FORBIDDEN_CLI_TOKENS:
                    self.assertNotIn(token, text, (cmd, token))


class TestStrictRequestContract(unittest.TestCase):
    def test_legacy_payloads_rejected(self):
        cases = (
            {"prompt": "x", "width": 1024},
            {"prompt": "x", "height": 1024},
            {"prompt": "x", "ref_type": "character"},
            {"prompt": "x", "denoise": 0.5},
            {"prompt": "x", "translator": "deepseek"},
            {"prompt": "x", "translator": "gemini"},
        )
        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    request = GenerateRequest.from_dict(payload)
                    request.validate()

    def test_valid_payloads_accepted(self):
        for payload in (
            {"prompt": "x", "translator": "auto"},
            {"prompt": "x", "translator": "off"},
            {"prompt": "x", "size": "1920x1080"},
        ):
            request = GenerateRequest.from_dict(payload)
            request.validate()

    def test_http_generate_rejects_legacy_width(self):
        server = ApiTestServer()
        self.addCleanup(server.close)
        status, _, data = server.json(
            "POST", "/api/v2/generate", {"prompt": "x", "width": 1024}
        )
        self.assertEqual(status, 400)
        self.assertEqual(data["error"]["type"], "validation_error")
        self.assertIn("width", data["error"]["message"])

    def test_http_transport_references_still_accepted(self):
        # references 是 HTTP transport 字段：resolver 消费后转换为 Core 字段，
        # 不应被 Core strict validation 误杀。
        from imagegen.services.assets import AssetService

        import tempfile

        from PIL import Image
        import io

        buf = io.BytesIO()
        Image.new("RGB", (8, 8), (1, 2, 3)).save(buf, format="PNG")
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            assets = AssetService(
                db_path=base / "imagegen.db",
                asset_dir=base / "assets" / "references",
            )
            record = assets.create_from_upload(buf.getvalue(), original_name="a.png")
            server = ApiTestServer(asset_service=assets)
            self.addCleanup(server.close)
            status, _, _ = server.json(
                "POST",
                "/api/v2/generate",
                {
                    "prompt": "x",
                    "references": [{"asset_id": record.asset_id, "role": "character"}],
                },
            )
            self.assertEqual(status, 200)
