"""Generation 落库 best-effort 测试：历史失败不影响生成成功。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from imagegen.models import GenerateRequest, GenerateResult
from imagegen.services.generation import GenerationService
from imagegen.services.history import HistoryService


def make_result(generation_id="a" * 32):
    return GenerateResult(
        path="out.png",
        backend="vertex",
        image_model_used="gemini-3-pro-image",
        seed=7,
        requested_size="1024x1024",
        actual_size="1024x1024",
        prompt_used="p",
        warnings=["w1"],
        generation_id=generation_id,
    )


class TestGenerationPersistence(unittest.TestCase):
    def test_generation_service_persists_history(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        history = HistoryService(Path(tmp.name) / "imagegen.db")

        class FakeEngine:
            def generate(self, request):
                return make_result(generation_id="9" * 32)

        svc = GenerationService(engine=FakeEngine(), history_service=history)
        result = svc.generate(GenerateRequest(prompt="hello", size="1024x1024"))
        self.assertEqual(result.warnings, ["w1"])
        record = history.get("9" * 32)
        self.assertIsNotNone(record)
        self.assertEqual(record.prompt, "hello")

    def test_history_failure_does_not_fail_generation(self):
        class BoomHistory:
            def record(self, request, result):
                raise RuntimeError("sqlite locked")

        class FakeEngine:
            def generate(self, request):
                return make_result(generation_id="8" * 32)

        svc = GenerationService(engine=FakeEngine(), history_service=BoomHistory())
        result = svc.generate(GenerateRequest(prompt="hello"))
        self.assertTrue(any("history persistence failed" in w for w in result.warnings))
        self.assertFalse(any("failed" in w and "history" not in w for w in result.warnings))

    def test_no_history_service_skips(self):
        class FakeEngine:
            def generate(self, request):
                return make_result(generation_id="7" * 32)

        svc = GenerationService(engine=FakeEngine(), history_service=None)
        result = svc.generate(GenerateRequest(prompt="hello"))
        self.assertEqual(result.warnings, ["w1"])
