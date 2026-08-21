"""请求级验证与 Backend capability 验证测试（全部 mock，不调用真实 API）。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from imagegen import engine as engine_mod
from imagegen.backends.base import BackendCapabilities
from imagegen.config import load_config
from imagegen.errors import ValidationError
from imagegen.models import GenerateRequest, validate_backend_request

from ._helpers import make_png_bytes


def make_request(**kwargs):
    defaults = {"prompt": "画一张图"}
    defaults.update(kwargs)
    return GenerateRequest(**defaults)


class TestRequestValidation(unittest.TestCase):
    def test_empty_prompt_rejected(self):
        with self.assertRaises(ValidationError):
            make_request(prompt="").validate()

    def test_whitespace_prompt_rejected(self):
        with self.assertRaises(ValidationError):
            make_request(prompt="   \n\t ").validate()

    def test_valid_sizes_accepted(self):
        for size in ("", "auto", "1024x1536", "1536x1024", "768×1408"):
            make_request(size=size).validate()

    def test_invalid_sizes_rejected(self):
        for size in ("abc", "1024", "1024*1536", "-1x1024", "0x0"):
            with self.assertRaises(ValidationError):
                make_request(size=size).validate()

    def test_width_without_height_rejected(self):
        with self.assertRaises(ValidationError):
            make_request(width=1024).validate()

    def test_out_of_range_dimensions_rejected(self):
        with self.assertRaises(ValidationError):
            make_request(width=5, height=5).validate()

    def test_seed_bool_rejected(self):
        with self.assertRaises(ValidationError):
            make_request(seed=True).validate()

    def test_seed_int_accepted(self):
        make_request(seed=42).validate()

    def test_denoise_out_of_range_rejected(self):
        with self.assertRaises(ValidationError):
            make_request(denoise=1.5).validate()

    def test_roles_exceed_images_rejected(self):
        with self.assertRaises(ValidationError):
            make_request(images=["a.png"], reference_roles=["character", "outfit"]).validate()

    def test_max_ref_images_rejected(self):
        with self.assertRaises(ValidationError):
            make_request(images=[f"img{i}.png" for i in range(5)]).validate()

    def test_single_and_multi_ref_accepted(self):
        make_request(images=["a.png"], reference_roles=["character"]).validate()
        make_request(
            images=["a.png", "b.png"],
            reference_roles=["character", "outfit"],
        ).validate()


class TestBackendCapabilityValidation(unittest.TestCase):
    def test_img2img_unsupported(self):
        caps = BackendCapabilities(text_to_image=True, image_to_image=False)
        with self.assertRaises(ValidationError):
            validate_backend_request(make_request(images=["a.png"]), caps)

    def test_multi_reference_unsupported(self):
        caps = BackendCapabilities(text_to_image=True, image_to_image=True, multi_reference=False)
        with self.assertRaises(ValidationError):
            validate_backend_request(
                make_request(images=["a.png", "b.png"]), caps
            )

    def test_quality_explicit_on_unsupported_backend(self):
        caps = BackendCapabilities(
            text_to_image=True, image_to_image=True, multi_reference=True, quality=False
        )
        with self.assertRaises(ValidationError):
            validate_backend_request(make_request(quality="high"), caps)
        # 未显式指定 quality 时忽略
        validate_backend_request(make_request(quality=""), caps)

    def test_supported_combination_passes(self):
        caps = BackendCapabilities(
            text_to_image=True,
            image_to_image=True,
            multi_reference=True,
            quality=True,
        )
        validate_backend_request(
            make_request(
                images=["a.png", "b.png"],
                reference_roles=["character", "outfit"],
                quality="high",
            ),
            caps,
        )


class _NoImg2ImgBackend:
    id = "no-img2img"

    def capabilities(self):
        return BackendCapabilities(
            text_to_image=True, image_to_image=False, multi_reference=False
        )

    def generate(self, cfg, prompt, width, height, model="", **kwargs):
        return make_png_bytes(width, height)


class TestEngineCapabilityIntegration(unittest.TestCase):
    def test_engine_rejects_img2img_for_unsupported_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            ref = Path(tmp) / "ref.png"
            ref.write_bytes(make_png_bytes(64, 64))
            cfg = load_config()
            cfg["save_dir"] = str(Path(tmp) / "out")
            cfg["reference"] = {"auto_classify": False, "vision_script": "", "classify_timeout": 90}
            with (
                mock.patch.object(engine_mod, "load_config", return_value=cfg),
                mock.patch.object(engine_mod, "get_backend", return_value=_NoImg2ImgBackend()),
            ):
                with self.assertRaises(ValidationError):
                    engine_mod.generate(
                        GenerateRequest(
                            prompt="改图",
                            width=1024,
                            height=1024,
                            translator="off",
                            images=[str(ref)],
                        )
                    )

    def test_engine_rejects_explicit_quality_on_vertex(self):
        """Vertex 不支持 quality：显式传 quality 必须报错而不是静默忽略。"""
        with tempfile.TemporaryDirectory() as tmp:
            cfg = load_config()
            cfg["save_dir"] = str(Path(tmp) / "out")
            with (
                mock.patch.object(engine_mod, "load_config", return_value=cfg),
                mock.patch.object(engine_mod, "get_backend", return_value=_NoImg2ImgBackend()),
            ):
                with self.assertRaises(ValidationError):
                    engine_mod.generate(
                        GenerateRequest(
                            prompt="画一张图",
                            width=1024,
                            height=1024,
                            translator="off",
                            quality="high",
                        )
                    )
