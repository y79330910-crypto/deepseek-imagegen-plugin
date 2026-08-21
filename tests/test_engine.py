"""Engine 回归测试：ref_type 单参考图、image_model_used、denoise 弃用、备用后端编排。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from imagegen import engine
from imagegen.config import load_config
from imagegen.errors import GenError
from imagegen.models import GenerateRequest

from ._helpers import FakeOpenAIBackend, FakeVertexBackend, make_png_bytes


class TestRefTypeSingleReference(unittest.TestCase):
    def test_ref_type_takes_effect_without_ref_role(self):
        """单参考图：没有 --ref-role 且 --ref-type != auto 时必须生效。"""
        with tempfile.TemporaryDirectory() as tmp:
            save_dir = Path(tmp) / "out"
            ref = Path(tmp) / "ref.png"
            ref.write_bytes(make_png_bytes(50, 50))
            cfg = load_config()
            cfg["save_dir"] = str(save_dir)
            # 有自动分类但找不到视觉桥接脚本 → 显式 ref_type 应直接生效
            cfg["reference"] = {"auto_classify": True, "vision_script": "", "classify_timeout": 90}
            with (
                mock.patch.object(engine, "load_config", return_value=cfg),
                mock.patch.object(engine, "get_backend", return_value=FakeVertexBackend()),
            ):
                result = engine.generate(
                    GenerateRequest(
                        prompt="给这张图换个场景",
                        width=1024,
                        height=1024,
                        translator="off",
                        images=[str(ref)],
                        ref_type="outfit",
                    )
                )
            self.assertEqual(result.reference["type"], "outfit")
            self.assertEqual(result.reference["method"], "manual")

    def test_multi_reference_still_uses_ref_roles(self):
        """多参考图仍以 --ref-role 为主。"""
        with tempfile.TemporaryDirectory() as tmp:
            save_dir = Path(tmp) / "out"
            ref1 = Path(tmp) / "ref1.png"
            ref1.write_bytes(make_png_bytes(64, 64))
            ref2 = Path(tmp) / "ref2.png"
            ref2.write_bytes(make_png_bytes(64, 64))
            cfg = load_config()
            cfg["save_dir"] = str(save_dir)
            cfg["reference"] = {"auto_classify": False, "vision_script": "", "classify_timeout": 90}
            with (
                mock.patch.object(engine, "load_config", return_value=cfg),
                mock.patch.object(engine, "get_backend", return_value=FakeVertexBackend()),
            ):
                result = engine.generate(
                    GenerateRequest(
                        prompt="保持角色，穿第二张图的服装",
                        width=1024,
                        height=1024,
                        translator="off",
                        images=[str(ref1), str(ref2)],
                        reference_roles=["character", "outfit"],
                        ref_type="style",
                    )
                )
            self.assertEqual(
                [it["type"] for it in result.reference["items"]],
                ["character", "outfit"],
            )


class TestImageModelUsed(unittest.TestCase):
    def test_result_reports_image_model_not_translator_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_dir = Path(tmp) / "out"
            cfg = load_config()
            cfg["save_dir"] = str(save_dir)
            cfg["mirror_dir"] = ""
            with (
                mock.patch.object(engine, "load_config", return_value=cfg),
                mock.patch.object(engine, "get_backend", return_value=FakeVertexBackend()),
                mock.patch.object(
                    engine,
                    "translate_prompt",
                    return_value={
                        "ok": True,
                        "engine": "deepseek",
                        "engine_used": "deepseek",
                        "model": "deepseek-v4-flash",
                        "original": "画一张图",
                        "rewritten": "rewritten prompt",
                        "fallback": False,
                    },
                ),
            ):
                result = engine.generate(
                    GenerateRequest(prompt="画一张图", width=1024, height=1024, seed=1)
                )
            self.assertEqual(result.image_model_used, "gemini-3-pro-image")
            self.assertEqual(result.model, "gemini-3-pro-image")
            self.assertEqual(result.translator.get("model"), "deepseek-v4-flash")
            self.assertNotEqual(result.image_model_used, result.translator.get("model"))
            data = result.to_dict()
            self.assertEqual(data["image_model_used"], "gemini-3-pro-image")


class TestDenoiseDeprecated(unittest.TestCase):
    def test_denoise_ignored_with_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_dir = Path(tmp) / "out"
            cfg = load_config()
            cfg["save_dir"] = str(save_dir)
            with (
                mock.patch.object(engine, "load_config", return_value=cfg),
                mock.patch.object(engine, "get_backend", return_value=FakeVertexBackend()),
            ):
                result = engine.generate(
                    GenerateRequest(
                        prompt="画一张图",
                        width=1024,
                        height=1024,
                        seed=2,
                        translator="off",
                        denoise=0.6,
                    )
                )
            self.assertTrue(any("denoise" in w and "弃用" in w for w in result.warnings))
            self.assertNotIn("denoise", result.to_dict())

    def test_denoise_out_of_range_rejected(self):
        with mock.patch.object(engine, "load_config", return_value=load_config()):
            with self.assertRaises(GenError):
                engine.generate(GenerateRequest(prompt="x", denoise=1.5))


class TestOpenAICompatFlow(unittest.TestCase):
    def test_extra_backend_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_dir = Path(tmp) / "out"
            cfg = load_config()
            cfg["save_dir"] = str(save_dir)
            cfg["extra_backends"] = {
                "dragtokens": {
                    "base_url": "https://draw.example.com/v1",
                    "api_key": "sk-test",
                    "model": "gpt-image-2",
                    "models": ["gpt-image-2", "gpt-image-2-4k超分"],
                    "sizes": "",
                    "quality": "auto",
                }
            }
            captured: dict = {}

            def fake_generate(cfg, prompt, width, height, model="", **kwargs):
                captured["prompt"] = prompt
                captured["model"] = model
                captured["size_str"] = kwargs.get("size_str")
                return make_png_bytes(width, height)

            fake = FakeOpenAIBackend("dragtokens")
            fake.generate = fake_generate
            with (
                mock.patch.object(engine, "load_config", return_value=cfg),
                mock.patch.object(engine, "get_backend", return_value=fake),
            ):
                result = engine.generate(
                    GenerateRequest(
                        prompt="横版海报",
                        width=1536,
                        height=1024,
                        seed=3,
                        translator="off",
                        backend="dragtokens",
                        quality="high",
                    )
                )
            self.assertEqual(result.backend, "dragtokens")
            self.assertEqual(result.image_model_used, "gpt-image-2")
            self.assertEqual(captured["size_str"], "1536x1024")
            self.assertIn("画面尺寸要求", captured["prompt"])
            self.assertEqual(captured["model"], "gpt-image-2")
            self.assertEqual(result.size_check["effective"], "1536x1024")
            self.assertTrue(Path(result.path).is_file())
