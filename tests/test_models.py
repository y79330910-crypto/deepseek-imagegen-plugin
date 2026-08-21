"""GenerateRequest / GenerateResult 统一数据模型测试。"""

from __future__ import annotations

import unittest

from imagegen.models import GenerateRequest, GenerateResult


class TestGenerateRequest(unittest.TestCase):
    def test_defaults(self):
        req = GenerateRequest(prompt="画一张图")
        self.assertEqual(req.prompt, "画一张图")
        self.assertIsNone(req.width)
        self.assertIsNone(req.height)
        self.assertEqual(req.model, "")
        self.assertEqual(req.backend, "")
        self.assertIsNone(req.seed)
        self.assertEqual(req.quality, "")
        self.assertEqual(req.composition, "auto")
        self.assertEqual(req.translator, "auto")
        self.assertEqual(req.size_policy, "")
        self.assertEqual(req.images, [])
        self.assertEqual(req.reference_roles, [])
        self.assertEqual(req.ref_type, "auto")
        self.assertIsNone(req.library_enabled)
        self.assertEqual(req.out, "")
        self.assertIsNone(req.denoise)

    def test_list_fields_are_independent(self):
        req = GenerateRequest(prompt="x", images=["a.png"], reference_roles=["character"])
        req.images.append("b.png")
        other = GenerateRequest(prompt="y")
        self.assertEqual(other.images, [])
        self.assertEqual(other.reference_roles, [])


class TestGenerateResult(unittest.TestCase):
    def test_defaults_and_to_dict(self):
        res = GenerateResult(
            path=r"C:\tmp\out.png",
            backend="vertex",
            image_model_used="gemini-3-pro-image",
            seed=7,
            requested_size="1024x1024",
            actual_size="1024x1024",
            prompt_used="prompt",
        )
        data = res.to_dict()
        self.assertTrue(data["ok"])
        self.assertEqual(data["image_model_used"], "gemini-3-pro-image")
        self.assertEqual(data["model"], "gemini-3-pro-image")  # 兼容别名
        self.assertEqual(data["size"], "1024x1024")
        self.assertNotIn("warnings", data)  # 无警告时不输出
        self.assertNotIn("init_images", data)
        self.assertNotIn("mirror_path", data)

    def test_to_dict_optional_fields(self):
        res = GenerateResult(
            path="out.png",
            backend="dragtokens",
            image_model_used="gpt-image-2",
            seed=1,
            requested_size="1024x1024",
            actual_size="1024x1024",
            prompt_used="p",
            warnings=["注意"],
            init_images=["ref.png"],
            mirror_path="mirror.png",
        )
        data = res.to_dict()
        self.assertEqual(data["warnings"], ["注意"])
        self.assertEqual(data["init_images"], ["ref.png"])
        self.assertEqual(data["mirror_path"], "mirror.png")
