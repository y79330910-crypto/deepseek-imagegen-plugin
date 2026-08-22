"""GenerateRequest / Translator prompt mode contracts."""

from __future__ import annotations

import unittest

from imagegen.errors import ValidationError
from imagegen.models import GenerateRequest
from imagegen.translator import build_translator_system
from imagegen.prompt_case import QueryAnalysis


class TestPromptModes(unittest.TestCase):
    def test_default_and_valid_modes(self):
        self.assertEqual(GenerateRequest(prompt="x").prompt_mode, "optimized")
        for mode in ("conservative", "optimized", "creative"):
            request = GenerateRequest.from_dict({"prompt": "x", "prompt_mode": mode})
            request.validate()
            self.assertEqual(request.to_dict()["prompt_mode"], mode)

    def test_invalid_mode_rejected(self):
        with self.assertRaises(ValidationError):
            GenerateRequest.from_dict({"prompt": "x", "prompt_mode": "wild"})

    def test_translator_declares_locked_priority_and_case_boundary(self):
        query = QueryAnalysis(query="画猫", locked={"subject": True})
        system = build_translator_system(prompt_mode="creative", query_analysis=query)
        self.assertIn("案例不是当前画面的素材来源", system)
        self.assertIn("Locked", system)
        self.assertIn("创新只能发生在 Open 字段", system)


if __name__ == "__main__":
    unittest.main()

