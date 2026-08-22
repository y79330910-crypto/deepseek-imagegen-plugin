"""Prompt Case migration tests; database and external APIs are mocked."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from imagegen import library
from imagegen.prompt_case import (
    PROMPT_CASE_EMBEDDING_VERSION,
    PROMPT_CASE_PARSER_VERSION,
    PromptCase,
    PromptFacets,
    parse_prompt_case,
)


class FakeCursor:
    def __init__(self, rows=(), fetchone_values=()):
        self.rows = list(rows)
        self.fetchone_values = list(fetchone_values)
        self.executed: list[tuple[str, object]] = []
        self.lastrowid = 100

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=()):  # noqa: ANN001
        self.executed.append((str(sql), params))

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        if self.fetchone_values:
            return self.fetchone_values.pop(0)
        return None


class FakeConnection:
    def __init__(self, cursor):
        self.fake_cursor = cursor
        self.closed = False

    def cursor(self):
        return self.fake_cursor

    def close(self):
        self.closed = True


def _case(requirement: str, content: str, *, visual: str = "") -> PromptCase:
    result = PromptCase(
        requirement=requirement,
        inferred_requirement="" if requirement else "推断需求",
        requirement_source="user" if requirement else "inferred",
        content=content,
        facets=PromptFacets(subject="主体", style="动漫"),
        intent_text=requirement or "需求：推断需求\n主体：主体",
        visual_text=visual,
        parser_version=PROMPT_CASE_PARSER_VERSION,
    )
    return result


class TestPromptCaseMigration(unittest.TestCase):
    def setUp(self):
        self.pl = {
            "embedding": {"model": "test-embedding"},
            "translator": {},
        }

    def test_invalid_parser_is_strict_for_rebuild_but_runtime_still_falls_back(self):
        runtime = parse_prompt_case("", "完整 Prompt", client=lambda *_args: "invalid")
        self.assertEqual(runtime.parser_version, 0)
        with self.assertRaises(Exception):
            parse_prompt_case(
                "",
                "完整 Prompt",
                client=lambda *_args: "invalid",
                strict=True,
            )

    def test_schema_upgrade_adds_new_columns_without_dropping_legacy_columns(self):
        cursor = FakeCursor(fetchone_values=[(0,)] * 32)
        with patch.object(library, "mysql_conn", return_value=FakeConnection(cursor)):
            library.init_db(self.pl)
        sql_text = "\n".join(sql for sql, _params in cursor.executed)
        for column in (
            "embedding",
            "requirement_embedding",
            "requirement",
            "inferred_requirement",
            "requirement_source",
            "facets_json",
            "intent_text",
            "visual_text",
            "intent_embedding",
            "visual_embedding",
            "parser_version",
            "embedding_model",
            "embedding_version",
        ):
            self.assertIn(column, sql_text)
        self.assertNotIn("DROP COLUMN embedding", sql_text)
        self.assertNotIn("DROP COLUMN requirement_embedding", sql_text)

    def test_rebuild_preserves_legacy_blobs_and_writes_new_metadata(self):
        select_cursor = FakeCursor(rows=[(1, "content one", "", "text_to_image")])
        update_cursor = FakeCursor(
            fetchone_values=[
                (1,),
                (1, "content one", "", "text_to_image", 0, None, 0, "", "", None),
            ]
        )
        connections = [FakeConnection(select_cursor), FakeConnection(update_cursor)]

        def fake_parser(requirement, content, **_kwargs):  # noqa: ANN001
            return _case(requirement, content, visual="构图：中景")

        with patch.object(library, "mysql_conn", side_effect=connections), patch.object(
            library, "parse_prompt_case", side_effect=fake_parser
        ), patch.object(
            library, "embed_texts", return_value=[[1.0, 0.0], [0.0, 1.0]]
        ):
            result = library.rebuild_cases(self.pl)

        self.assertEqual(result, {"total": 1, "success": 1, "failed": 0, "skipped": 0})
        sql, params = next(
            (sql, params)
            for sql, params in update_cursor.executed
            if "UPDATE prompts SET" in sql
        )
        self.assertIn("intent_embedding", sql)
        self.assertNotIn(" embedding=%s", sql)
        self.assertNotIn(" requirement_embedding=%s", sql)
        self.assertEqual(params[-3:], ("test-embedding", PROMPT_CASE_EMBEDDING_VERSION, 1))
        self.assertIn("GET_LOCK", update_cursor.executed[0][0])
        self.assertIn("RELEASE_LOCK", update_cursor.executed[-1][0])

    def test_one_failed_row_does_not_stop_following_rows(self):
        select_cursor = FakeCursor(
            rows=[
                (1, "content one", "", "text_to_image"),
                (2, "content two", "真实需求", "text_to_image"),
            ]
        )
        update_cursor = FakeCursor(
            fetchone_values=[
                (1,),
                (1, "content one", "", "text_to_image", 0, None, 0, "", "", None),
            ]
        )
        update_cursor_two = FakeCursor(
            fetchone_values=[
                (1,),
                (2, "content two", "真实需求", "text_to_image", 0, None, 0, "", "", None),
            ]
        )
        connections = [
            FakeConnection(select_cursor),
            FakeConnection(update_cursor),
            FakeConnection(update_cursor_two),
        ]
        calls = {"count": 0}

        def fake_parser(requirement, content, **_kwargs):  # noqa: ANN001
            calls["count"] += 1
            if calls["count"] == 1:
                raise ValueError("bad parser JSON")
            return _case(requirement, content)

        with patch.object(library, "mysql_conn", side_effect=connections), patch.object(
            library, "parse_prompt_case", side_effect=fake_parser
        ), patch.object(library, "embed_texts", return_value=[[1.0, 0.0]]):
            result = library.rebuild_cases(self.pl)

        self.assertEqual(result["total"], 2)
        self.assertEqual(result["success"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(
            sum("UPDATE prompts SET" in sql for sql, _params in update_cursor.executed)
            + sum("UPDATE prompts SET" in sql for sql, _params in update_cursor_two.executed),
            1,
        )

    def test_empty_visual_text_does_not_request_visual_embedding(self):
        select_cursor = FakeCursor(rows=[(1, "content one", "", "text_to_image")])
        update_cursor = FakeCursor(
            fetchone_values=[
                (1,),
                (1, "content one", "", "text_to_image", 0, None, 0, "", "", None),
            ]
        )
        calls = []

        def fake_embed(_pl, texts, input_type=""):  # noqa: ANN001
            calls.append((list(texts), input_type))
            return [[1.0, 0.0]]

        with patch.object(
            library, "mysql_conn", side_effect=[FakeConnection(select_cursor), FakeConnection(update_cursor)]
        ), patch.object(
            library, "parse_prompt_case", return_value=_case("", "content one", visual="")
        ), patch.object(library, "embed_texts", side_effect=fake_embed):
            result = library.rebuild_cases(self.pl)

        self.assertEqual(result["success"], 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(calls[0][0]), 1)
        update_params = next(
            params
            for sql, params in update_cursor.executed
            if "UPDATE prompts SET" in sql
        )
        self.assertIsNone(update_params[8])

    def test_new_prompt_writes_new_vectors_without_requiring_legacy_vectors(self):
        check_cursor = FakeCursor(fetchone_values=[None])
        insert_cursor = FakeCursor(fetchone_values=[None])
        insert_cursor.lastrowid = 101

        with patch.object(
            library,
            "mysql_conn",
            side_effect=[FakeConnection(check_cursor), FakeConnection(insert_cursor)],
        ), patch.object(
            library,
            "parse_prompt_case",
            return_value=_case("真实需求", "完整 Prompt", visual="风格：动漫"),
        ), patch.object(
            library,
            "embed_texts",
            return_value=[[1.0, 0.0], [0.0, 1.0]],
        ):
            result = library.add_prompt(self.pl, "完整 Prompt", requirement="真实需求")

        self.assertTrue(result["added"])
        sql, params = insert_cursor.executed[-1]
        self.assertIn("embedding_model", sql)
        self.assertIn("embedding_version", sql)
        self.assertIsNone(params[7])
        self.assertIsNone(params[8])
        self.assertEqual(params[20], "test-embedding")
        self.assertEqual(params[21], PROMPT_CASE_EMBEDDING_VERSION)

    def test_default_selection_contains_all_migration_conditions_and_limit(self):
        cursor = FakeCursor(rows=[])
        with patch.object(library, "mysql_conn", return_value=FakeConnection(cursor)):
            library.rebuild_cases(self.pl, limit=7)
        sql, params = cursor.executed[0]
        self.assertIn("archived=0", sql)
        self.assertIn("parser_version", sql)
        self.assertIn("intent_embedding IS NULL", sql)
        self.assertIn("embedding_version", sql)
        self.assertIn("embedding_model", sql)
        self.assertIn("visual_embedding IS NULL", sql)
        self.assertTrue(str(sql).rstrip().endswith("LIMIT %s"))
        self.assertEqual(params[-1], 7)

    def test_rebuild_skips_a_row_when_another_worker_holds_its_lock(self):
        select_cursor = FakeCursor(rows=[(1, "content one", "", "text_to_image")])
        worker_cursor = FakeCursor(fetchone_values=[(0,)])
        with patch.object(
            library,
            "mysql_conn",
            side_effect=[FakeConnection(select_cursor), FakeConnection(worker_cursor)],
        ), patch.object(library, "parse_prompt_case") as parser:
            result = library.rebuild_cases(self.pl, workers=4)

        self.assertEqual(result, {"total": 1, "success": 0, "failed": 0, "skipped": 1})
        parser.assert_not_called()
        self.assertIn("GET_LOCK", worker_cursor.executed[0][0])

    def test_workers_must_be_between_one_and_thirty_two(self):
        with self.assertRaises(library.LibError):
            library.rebuild_cases(self.pl, workers=0)
        with self.assertRaises(library.LibError):
            library.rebuild_cases(self.pl, workers=33)

    def test_rebuild_parser_exposes_workers_option(self):
        args = library.build_parser().parse_args(["rebuild-cases", "--workers", "4"])
        self.assertEqual(args.workers, 4)

    def test_backup_is_text_only_and_contains_case_fields(self):
        row = (
            "content", "zh", "插画", "tag", "source", "url", "notes", "req",
            "inferred", "inferred", "text_to_image", json.dumps({"subject": "猫"}),
            "[]", "需求：猫", "风格：动漫", 1, "test-embedding", 1,
        )
        cursor = FakeCursor(rows=[row])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "backup.jsonl"
            with patch.object(library, "mysql_conn", return_value=FakeConnection(cursor)):
                result = library.backup(self.pl, str(path))
            self.assertEqual(result["count"], 1)
            data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["inferred_requirement"], "inferred")
        self.assertEqual(data["intent_text"], "需求：猫")
        self.assertEqual(data["visual_text"], "风格：动漫")
        self.assertNotIn("embedding", data)
        self.assertNotIn("intent_embedding", data)

    def test_stats_reports_case_readiness_and_current_versions(self):
        cursor = FakeCursor(
            rows=[("插画", 3)],
            fetchone_values=[(5,), (1,), (2,)],
        )
        with patch.object(library, "mysql_conn", return_value=FakeConnection(cursor)):
            result = library.stats(self.pl)
        self.assertEqual(result["total"], 5)
        self.assertEqual(result["active"], 4)
        self.assertEqual(result["archived"], 1)
        self.assertEqual(result["case_ready"], 2)
        self.assertEqual(result["case_pending"], 3)
        self.assertEqual(result["parser_version_current"], PROMPT_CASE_PARSER_VERSION)
        self.assertEqual(result["embedding_version_current"], PROMPT_CASE_EMBEDDING_VERSION)


class TestPromptCaseFallback(unittest.TestCase):
    def test_intent_fallback_priority(self):
        intent = library._pack_vec([1.0, 0.0])
        requirement = library._pack_vec([0.0, 1.0])
        content = library._pack_vec([1.0, 1.0])
        self.assertEqual(library._pick_intent_vec(intent, requirement, content), [1.0, 0.0])
        self.assertEqual(library._pick_intent_vec(None, requirement, content), [0.0, 1.0])
        self.assertEqual(library._pick_intent_vec(None, None, content), [1.0, 1.0])


if __name__ == "__main__":
    unittest.main()
