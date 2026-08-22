"""Prompt Case core tests; all parser calls use deterministic fakes."""

from __future__ import annotations

import unittest

from imagegen.prompt_case import (
    FACET_FIELDS,
    MODE_PROFILES,
    PromptCase,
    build_intent_text,
    build_visual_text,
    format_prompt_case,
    parse_prompt_case,
    parse_query,
    select_diverse_cases,
)


class FakeParser:
    def __init__(self, output: str):
        self.output = output

    def generate_text(self, messages, model, **kwargs):  # noqa: ANN001
        return self.output


class TestPromptCaseParser(unittest.TestCase):
    def test_fenced_json_is_normalized_to_fixed_schema(self):
        case = parse_prompt_case(
            "",
            "雨中的猫",
            client=FakeParser(
                '```json\n{"facets":{"subject":"猫","action":"行走",'
                '"unknown":"drop"},"transferable_lessons":["通过步态表达动态"]}\n```'
            ),
        )
        self.assertEqual(set(case.facets.to_dict()), set(FACET_FIELDS))
        self.assertEqual(case.facets.subject, "猫")
        self.assertEqual(case.facets.action, "行走")
        self.assertEqual(case.transferable_lessons, ["通过步态表达动态"])
        self.assertEqual(case.parser_version, 1)
        self.assertEqual(case.content, "雨中的猫")

    def test_invalid_json_degrades_without_losing_content(self):
        warnings = []
        case = parse_prompt_case(
            "真实需求",
            "原始完整 Prompt",
            client=FakeParser("not json"),
            warning=warnings.append,
        )
        self.assertEqual(case.parser_version, 0)
        self.assertEqual(case.content, "原始完整 Prompt")
        self.assertEqual(case.requirement, "真实需求")
        self.assertEqual(case.intent_text, "真实需求")
        self.assertTrue(warnings)

    def test_real_requirement_wins_over_inferred(self):
        case = parse_prompt_case(
            "用户明确需求",
            "外部 Prompt",
            client=FakeParser(
                '{"inferred_requirement":"模型猜测","facets":{}}'
            ),
        )
        self.assertEqual(case.requirement, "用户明确需求")
        self.assertEqual(case.inferred_requirement, "")
        self.assertEqual(case.requirement_source, "user")

    def test_query_locked_fields_and_builders(self):
        query = parse_query(
            "洛天依撑伞行走",
            client=FakeParser(
                '{"facets":{"subject":"洛天依","action":"撑伞行走",'
                '"lighting":"霓虹反射"},"locked":{"subject":true,"action":true}}'
            ),
        )
        self.assertTrue(query.locked["subject"])
        self.assertFalse(query.locked["lighting"])
        self.assertIn("主体：洛天依", build_intent_text(query))
        self.assertIn("光线：霓虹反射", build_visual_text(query))


class TestPromptCaseSelection(unittest.TestCase):
    def test_modes_have_distinct_selection_profiles(self):
        self.assertEqual(MODE_PROFILES["conservative"]["final_k"], 3)
        self.assertEqual(MODE_PROFILES["optimized"]["final_k"], 4)
        self.assertGreater(
            MODE_PROFILES["creative"]["diversity_strength"],
            MODE_PROFILES["optimized"]["diversity_strength"],
        )

    def test_mmr_avoids_repeating_visual_expression(self):
        candidates = [
            {"id": 1, "rerank_score": 0.95, "visual_vector": [1.0, 0.0]},
            {"id": 2, "rerank_score": 0.94, "visual_vector": [0.999, 0.001]},
            {"id": 3, "rerank_score": 0.80, "visual_vector": [0.0, 1.0]},
        ]
        selected = select_diverse_cases(candidates, final_k=2, prompt_mode="creative")
        self.assertEqual([item["id"] for item in selected], [1, 3])

    def test_case_format_contains_structure_and_lessons(self):
        case = PromptCase(
            requirement="画猫",
            content="一只猫，电影感光线",
            transferable_lessons=["通过轮廓光分离主体"],
        )
        rendered = format_prompt_case(case)
        self.assertIn("原始需求：", rendered)
        self.assertIn("可迁移技巧：", rendered)
        self.assertIn("优秀提示词：", rendered)


if __name__ == "__main__":
    unittest.main()
