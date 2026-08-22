"""Engine 回归测试：参考图角色、image_model_used、尺寸/质量透传。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from imagegen import engine
from imagegen.config import load_config
from imagegen.errors import GenError
from imagegen.models import GenerateRequest

from ._helpers import FakeOpenAIClient, make_png_bytes


def make_image_cfg(tmp: str, model: str = "gemini-3-pro-image") -> dict:
    cfg = load_config()
    cfg["save_dir"] = str(Path(tmp) / "out")
    cfg["mirror_dir"] = ""
    cfg["image"] = {
        "base_url": "https://example.com/v1",
        "api_key": "sk-test",
        "model": model,
        "quality": "",
    }
    return cfg


class TestReferenceRoles(unittest.TestCase):
    def test_single_reference_without_role_auto_falls_back(self):
        """单参考图且未给 role：自动识别失败时按现有降级规则处理。"""
        with tempfile.TemporaryDirectory() as tmp:
            ref = Path(tmp) / "ref.png"
            ref.write_bytes(make_png_bytes(50, 50))
            cfg = make_image_cfg(tmp)
            # 自动分类开启但没有视觉桥接脚本 → 降级为文字判断
            cfg["reference"] = {"auto_classify": True, "vision_script": "", "classify_timeout": 90}
            fake = FakeOpenAIClient()
            with (
                mock.patch.object(engine, "load_config", return_value=cfg),
                mock.patch.object(engine, "OpenAIClient", return_value=fake),
            ):
                result = engine.generate(
                    GenerateRequest(
                        prompt="给这张图换个场景",
                        size="1024x1024",
                        translator="off",
                        images=[str(ref)],
                    )
                )
            self.assertEqual(result.reference["type"], "character")
            self.assertEqual(fake.requests[0]["kind"], "edit_image")

    def test_multi_reference_still_uses_ref_roles(self):
        """多参考图以 reference_roles 顺序对应。"""
        with tempfile.TemporaryDirectory() as tmp:
            ref1 = Path(tmp) / "ref1.png"
            ref1.write_bytes(make_png_bytes(64, 64))
            ref2 = Path(tmp) / "ref2.png"
            ref2.write_bytes(make_png_bytes(64, 64))
            cfg = make_image_cfg(tmp)
            cfg["reference"] = {"auto_classify": False, "vision_script": "", "classify_timeout": 90}
            fake = FakeOpenAIClient()
            with (
                mock.patch.object(engine, "load_config", return_value=cfg),
                mock.patch.object(engine, "OpenAIClient", return_value=fake),
            ):
                result = engine.generate(
                    GenerateRequest(
                        prompt="保持角色，穿第二张图的服装",
                        size="1024x1024",
                        translator="off",
                        images=[str(ref1), str(ref2)],
                        reference_roles=["character", "outfit"],
                    )
                )
            self.assertEqual(
                [it["type"] for it in result.reference["items"]],
                ["character", "outfit"],
            )


class TestImageModelUsed(unittest.TestCase):
    def test_result_reports_image_model_not_translator_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_image_cfg(tmp, model="gemini-3-pro-image")
            fake = FakeOpenAIClient()
            with (
                mock.patch.object(engine, "load_config", return_value=cfg),
                mock.patch.object(engine, "OpenAIClient", return_value=fake),
                mock.patch.object(
                    engine,
                    "translate_prompt",
                    return_value={
                        "ok": True,
                        "model": "tr-model",
                        "original": "画一张图",
                        "rewritten": "rewritten prompt",
                    },
                ),
            ):
                result = engine.generate(
                    GenerateRequest(prompt="画一张图", size="1024x1024", seed=1)
                )
            self.assertEqual(result.image_model_used, "gemini-3-pro-image")
            self.assertEqual(result.model, "gemini-3-pro-image")
            self.assertEqual(result.translator.get("model"), "tr-model")
            self.assertNotEqual(result.image_model_used, result.translator.get("model"))
            self.assertEqual(result.backend, "openai")
            data = result.to_dict()
            self.assertEqual(data["image_model_used"], "gemini-3-pro-image")


class TestSizeAndQualityPassthrough(unittest.TestCase):
    def test_size_sent_verbatim_without_quality(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_image_cfg(tmp)
            fake = FakeOpenAIClient()
            with (
                mock.patch.object(engine, "load_config", return_value=cfg),
                mock.patch.object(engine, "OpenAIClient", return_value=fake),
            ):
                result = engine.generate(
                    GenerateRequest(prompt="海报", size="1920x1080", seed=5, translator="off")
                )
            req = fake.requests[0]
            self.assertEqual(req["size"], "1920x1080")
            self.assertEqual(req["quality"], "")
            self.assertEqual(result.requested_size, "1920x1080")
            self.assertTrue(Path(result.path).is_file())

    def test_quality_sent_when_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_image_cfg(tmp)
            fake = FakeOpenAIClient()
            with (
                mock.patch.object(engine, "load_config", return_value=cfg),
                mock.patch.object(engine, "OpenAIClient", return_value=fake),
            ):
                engine.generate(
                    GenerateRequest(
                        prompt="海报", size="1080x1920", seed=5, translator="off", quality="high"
                    )
                )
            req = fake.requests[0]
            self.assertEqual(req["size"], "1080x1920")
            self.assertEqual(req["quality"], "high")

    def test_edit_flow_uses_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            ref = Path(tmp) / "ref.png"
            ref.write_bytes(make_png_bytes(40, 40))
            cfg = make_image_cfg(tmp)
            cfg["reference"] = {"auto_classify": False, "vision_script": "", "classify_timeout": 90}
            fake = FakeOpenAIClient()
            with (
                mock.patch.object(engine, "load_config", return_value=cfg),
                mock.patch.object(engine, "OpenAIClient", return_value=fake),
            ):
                result = engine.generate(
                    GenerateRequest(
                        prompt="参考图改图",
                        size="1600x900",
                        translator="off",
                        images=[str(ref)],
                        reference_roles=["character"],
                    )
                )
            req = fake.requests[0]
            self.assertEqual(req["kind"], "edit_image")
            self.assertEqual(req["size"], "1600x900")
            self.assertEqual(len(req["images"]), 1)
            self.assertTrue(result.init_images)


class TestMissingImageConfig(unittest.TestCase):
    def test_engine_requires_image_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = load_config()
            cfg["save_dir"] = str(Path(tmp) / "out")
            cfg["image"] = {"base_url": "", "api_key": "", "model": "", "quality": ""}
            with (
                mock.patch.object(engine, "load_config", return_value=cfg),
                self.assertRaises(GenError),
            ):
                engine.generate(
                    GenerateRequest(prompt="画一张图", size="1024x1024", translator="off")
                )
