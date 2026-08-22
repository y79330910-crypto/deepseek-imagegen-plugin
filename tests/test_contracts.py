"""数据契约回归：CLI JSON 输出 / round-trip / Service 流程。"""

from __future__ import annotations

import io
import json
import unittest
from unittest import mock

from imagegen import GenerationService
from imagegen.cli import main as cli_main
from imagegen.models import GenerateRequest, GenerateResult


class FakeResultEngine:
    def generate(self, request):
        return GenerateResult(
            path=r"C:\tmp\out.png",
            image_model_used="gemini-3-pro-image",
            requested_size="1024x1024",
            actual_size="1024x1024",
            prompt_used="p",
            warnings=["w1"],
        )


class TestCliJsonContract(unittest.TestCase):
    def test_cli_generate_json_contains_generation_id(self):
        buf = io.StringIO()
        with (
            mock.patch("imagegen.cli.GenerationService") as svc_cls,
            mock.patch("sys.stdout", buf),
        ):
            svc_cls.return_value.generate.return_value = FakeResultEngine().generate(None)
            code = cli_main(["generate", "hello", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(buf.getvalue())
        self.assertIn("generation_id", data)
        self.assertEqual(len(data["generation_id"]), 32)
        self.assertEqual(data["warnings"], ["w1"])
        self.assertEqual(data["image_model_used"], "gemini-3-pro-image")


class TestRequestRoundTripContract(unittest.TestCase):
    def test_json_string_round_trip(self):
        data = {
            "prompt": "a girl under cherry blossoms",
            "size": "1024x1536",
            "quality": "high",
            "images": ["a.png", "b.png"],
            "reference_roles": ["character", "outfit"],
        }
        request = GenerateRequest.from_dict(json.loads(json.dumps(data)))
        self.assertEqual(request.size, "1024x1536")
        restored = GenerateRequest.from_dict(request.to_dict())
        self.assertEqual(restored.to_dict(), request.to_dict())

    def test_service_flow_from_dict_to_result(self):
        request = GenerateRequest.from_dict({"prompt": "hello", "size": "auto"})
        svc = GenerationService(engine=FakeResultEngine())
        result = svc.generate(request)
        payload = json.dumps(result.to_dict())
        self.assertIn('"generation_id"', payload)
