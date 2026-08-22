"""请求级验证测试（全部 mock，不调用真实 API）。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from imagegen import engine as engine_mod
from imagegen.config import load_config
from imagegen.errors import ValidationError
from imagegen.models import GenerateRequest

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
