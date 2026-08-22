"""Phase 6 尺寸行为测试：原样透传 + 不符只警告、不重试、不归一化。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from imagegen import engine
from imagegen.config import load_config
from imagegen.models import GenerateRequest

from ._helpers import FakeOpenAIClient


def make_image_cfg(tmp: str) -> dict:
    cfg = load_config()
    cfg["save_dir"] = str(Path(tmp) / "out")
    cfg["mirror_dir"] = ""
    cfg["image"] = {
        "base_url": "https://example.com/v1",
        "api_key": "sk-test",
        "model": "gemini-3-pro-image",
        "quality": "",
    }
    return cfg


class TestLegacySizeInfrastructureRemoved(unittest.TestCase):
    def test_backends_module_removed(self):
        with self.assertRaises(ImportError):
            import imagegen.backends  # noqa: F401

    def test_normalize_functions_removed(self):
        import imagegen.image_utils as iu

        self.assertFalse(hasattr(iu, "canvas_size_for"))
        self.assertFalse(hasattr(iu, "build_canvas_png"))
        self.assertFalse(hasattr(iu, "fit_reference_to_canvas"))
        self.assertFalse(hasattr(iu, "size_matches"))
        self.assertFalse(hasattr(iu, "VERTEX_CANVAS_DEFAULTS"))


class TestSizeVerbatimPassthrough(unittest.TestCase):
    def test_sizes_sent_verbatim(self):
        for size in ("1920x1080", "1080x1920", "3440x1440", "1600x900"):
            with self.subTest(size=size):
                with tempfile.TemporaryDirectory() as tmp:
                    cfg = make_image_cfg(tmp)
                    fake = FakeOpenAIClient()
                    with (
                        mock.patch.object(engine, "load_config", return_value=cfg),
                        mock.patch.object(engine, "OpenAIClient", return_value=fake),
                    ):
                        result = engine.generate(
                            GenerateRequest(prompt="x", size=size, translator="off")
                        )
                    self.assertEqual(fake.requests[0]["size"], size)
                    self.assertEqual(result.requested_size, size)


class TestSizeMismatchWarning(unittest.TestCase):
    def test_mismatch_warns_without_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_image_cfg(tmp)
            fake = FakeOpenAIClient(size=(1536, 1024))
            with (
                mock.patch.object(engine, "load_config", return_value=cfg),
                mock.patch.object(engine, "OpenAIClient", return_value=fake),
            ):
                result = engine.generate(
                    GenerateRequest(prompt="x", size="1920x1080", translator="off")
                )
            self.assertFalse(result.size_match)
            self.assertTrue(
                any("输出尺寸与请求不符" in w for w in result.warnings)
            )
            # 不自动重试：上游只被调用一次
            self.assertEqual(len(fake.requests), 1)
            self.assertEqual(fake.requests[0]["size"], "1920x1080")
            self.assertEqual(result.size_check["requested"], "1920x1080")
            self.assertEqual(result.size_check["actual"], "1536x1024")

    def test_size_check_disabled_no_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_image_cfg(tmp)
            cfg["size_check"] = {"enabled": False, "tolerance": 0.06}
            fake = FakeOpenAIClient(size=(1536, 1024))
            with (
                mock.patch.object(engine, "load_config", return_value=cfg),
                mock.patch.object(engine, "OpenAIClient", return_value=fake),
            ):
                result = engine.generate(
                    GenerateRequest(prompt="x", size="1920x1080", translator="off")
                )
            self.assertFalse(result.size_match)
            self.assertEqual(result.warnings, [])
