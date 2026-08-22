"""GenerateRequest / GenerateResult 统一数据模型与序列化契约测试。"""

from __future__ import annotations

import json
import unittest

from imagegen.errors import ValidationError
from imagegen.models import GenerateRequest, GenerateResult


class TestGenerateRequest(unittest.TestCase):
    def test_defaults(self):
        req = GenerateRequest(prompt="画一张图")
        self.assertEqual(req.prompt, "画一张图")
        self.assertEqual(req.size, "")
        self.assertEqual(req.model, "")
        self.assertIsNone(req.seed)
        self.assertEqual(req.quality, "")
        self.assertEqual(req.composition, "auto")
        self.assertEqual(req.translator, "auto")
        self.assertEqual(req.images, [])
        self.assertEqual(req.reference_roles, [])
        self.assertIsNone(req.library_enabled)
        self.assertEqual(req.out, "")
        self.assertEqual(req.to_dict()["size"], "")

    def test_list_fields_are_independent(self):
        req = GenerateRequest(prompt="x", images=["a.png"], reference_roles=["character"])
        req.images.append("b.png")
        other = GenerateRequest(prompt="y")
        self.assertEqual(other.images, [])
        self.assertEqual(other.reference_roles, [])

    def test_from_dict_uses_defaults(self):
        req = GenerateRequest.from_dict({"prompt": "a girl under cherry blossoms"})
        self.assertEqual(req.prompt, "a girl under cherry blossoms")
        self.assertEqual(req.size, "")
        self.assertEqual(req.composition, "auto")
        self.assertEqual(req.images, [])

    def test_from_dict_full_input(self):
        data = {
            "prompt": "a girl under cherry blossoms",
            "model": "",
            "size": "1024x1536",
            "quality": "",
            "composition": "auto",
            "translator": "auto",
            "images": [],
            "reference_roles": [],
        }
        req = GenerateRequest.from_dict(data)
        self.assertEqual(req.size, "1024x1536")

    def test_from_dict_rejects_legacy_backend_field(self):
        with self.assertRaises(ValidationError):
            GenerateRequest.from_dict({"prompt": "x", "backend": "vertex"})

    def test_from_dict_rejects_unknown_fields(self):
        with self.assertRaises(ValidationError):
            GenerateRequest.from_dict({"prompt": "x", "promt": "typo"})
        with self.assertRaises(ValidationError):
            GenerateRequest.from_dict({"prompt": "x", "nonsense": 1})

    def test_from_dict_rejects_wrong_types(self):
        with self.assertRaises(ValidationError):
            GenerateRequest.from_dict({"prompt": "x", "seed": True})
        with self.assertRaises(ValidationError):
            GenerateRequest.from_dict({"prompt": "x", "images": "a.png"})
        with self.assertRaises(ValidationError):
            GenerateRequest.from_dict({"prompt": "x", "seed": "1024"})

    def test_request_round_trip_is_normalized(self):
        data = {
            "prompt": "hello",
            "size": "1024x1536",
            "images": ["a.png", "b.png"],
            "reference_roles": ["character", "outfit"],
        }
        req = GenerateRequest.from_dict(data)
        self.assertEqual(req.to_dict()["prompt"], "hello")
        self.assertEqual(req.to_dict()["size"], "1024x1536")
        self.assertEqual(req.to_dict()["images"], ["a.png", "b.png"])
        self.assertEqual(req.to_dict()["reference_roles"], ["character", "outfit"])
        again = GenerateRequest.from_dict(req.to_dict())
        self.assertEqual(again.to_dict(), req.to_dict())

    def test_request_to_dict_is_json_safe(self):
        req = GenerateRequest(prompt="x", images=["a.png"], seed=5)
        json.dumps(req.to_dict())


class TestGenerateResult(unittest.TestCase):
    def test_defaults_and_to_dict(self):
        res = GenerateResult(
            path=r"C:\tmp\out.png",
            image_model_used="gemini-3-pro-image",
            seed=7,
            requested_size="1024x1024",
            actual_size="1024x1024",
            prompt_used="prompt",
        )
        data = res.to_dict()
        self.assertTrue(data["ok"])
        self.assertEqual(data["image_model_used"], "gemini-3-pro-image")
        self.assertEqual(data["requested_size"], "1024x1024")
        self.assertEqual(data["actual_size"], "1024x1024")
        self.assertEqual(data["warnings"], [])  # warnings 永远是 list
        self.assertEqual(data["init_images"], [])
        self.assertEqual(data["mirror_path"], "")
        self.assertNotIn("backend", data)
        self.assertNotIn("model", data)
        self.assertNotIn("size", data)
        self.assertIn("generation_id", data)
        self.assertRegex(data["generation_id"], r"^[0-9a-f]{32}$")
        json.dumps(data)  # JSON-safe

    def test_to_dict_optional_fields(self):
        res = GenerateResult(
            path="out.png",
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

    def test_generation_id_autogenerated_unique(self):
        a = GenerateResult(
            path="a.png", image_model_used="m", seed=1,
            requested_size="1x1", actual_size="1x1", prompt_used="p",
        )
        b = GenerateResult(
            path="b.png", image_model_used="m", seed=2,
            requested_size="1x1", actual_size="1x1", prompt_used="p",
        )
        self.assertNotEqual(a.generation_id, b.generation_id)
        self.assertRegex(a.generation_id, r"^[0-9a-f]{32}$")
