#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepSeek ImageGen v1.0.0 冒烟测试（单文件）。

覆盖：配置合并与密钥打码、尺寸工具、模型挑选、构图预设、翻译官 off、
角色注入专项、参考图适配与降级、出图编排（模拟后端）、输出路径与镜像副本、
CLI JSON 输出、词库统计（演练库，无网络）。

运行：python scripts/tests/run_smoke_test.py
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = SCRIPT_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from imagegen import characters, generate  # noqa: E402
from imagegen.composition import resolve_composition  # noqa: E402
from imagegen.config import load_config, mask_config  # noqa: E402
from imagegen.http import GenError  # noqa: E402
from imagegen.image_utils import (  # noqa: E402
    aspect_ratio_key,
    canvas_size_for,
    default_output_path,
    fit_reference_to_canvas,
    mirror_output,
    parse_size,
    probe_image_size,
    sizes_match,
    slugify,
)
from imagegen.translator import translate_prompt  # noqa: E402
from imagegen.vertex import pick_best_image_model, pick_best_text_model  # noqa: E402


def make_png_bytes(width: int = 64, height: int = 64) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (120, 180, 240)).save(buf, format="PNG")
    return buf.getvalue()


class TestConfig(unittest.TestCase):
    def test_merge_and_mask(self):
        cfg = load_config()
        self.assertIn("characters", cfg)
        self.assertIn("洛天依", cfg["characters"])
        self.assertIn("presets", cfg["composition"])
        safe = mask_config(cfg)
        text = json.dumps(safe, ensure_ascii=False)
        self.assertNotIn(cfg.get("vertex", {}).get("api_key") or "sk-NOT-SET", text)
        self.assertIn("(未设置)", text)


class TestImageUtils(unittest.TestCase):
    def test_parse_size(self):
        self.assertEqual(parse_size("1024x768"), (1024, 768))
        self.assertEqual(parse_size("768×1408"), (768, 1408))
        with self.assertRaises(GenError):
            parse_size("abc")

    def test_slugify(self):
        self.assertEqual(slugify("画一张 洛天依 -- 全身"), "画一张-洛天依-全身")

    def test_aspect_and_canvas(self):
        self.assertEqual(aspect_ratio_key(768, 1408), (9, 16))
        self.assertEqual(canvas_size_for(768, 1408), (768, 1408))
        self.assertEqual(canvas_size_for(1024, 1024), (1024, 1024))

    def test_sizes_match(self):
        self.assertTrue(sizes_match((768, 1408), (768, 1408))["ok"])
        self.assertTrue(sizes_match((768, 1408), (1152, 2112))["ok"])  # 画幅一致（9:16）
        self.assertFalse(sizes_match((768, 1408), (1408, 768))["ok"])  # 方向不符
        self.assertFalse(sizes_match((1024, 1024), (1408, 768))["ok"])

    def test_probe_and_fit(self):
        png = make_png_bytes(32, 64)
        self.assertEqual(probe_image_size(png), (32, 64))
        fitted, mime, name = fit_reference_to_canvas(png, "image/png", 100, 100)
        self.assertEqual(mime, "image/png")
        self.assertEqual(probe_image_size(fitted), (100, 100))
        self.assertEqual(name, "reference-fit.png")


class TestModelPicking(unittest.TestCase):
    def test_pick_best_image_model(self):
        models = ["gemini-2.5-flash-image-preview", "gemini-3-pro-image", "imagen-4.0"]
        self.assertEqual(pick_best_image_model(models), "gemini-3-pro-image")

    def test_pick_best_text_model(self):
        models = ["gemini-3-pro-image", "gemini-3-pro", "gemini-2.5-flash"]
        self.assertNotIn("image", pick_best_text_model(models))


class TestComposition(unittest.TestCase):
    def test_resolve(self):
        cfg = load_config()
        self.assertEqual(resolve_composition("全身", cfg), "full-body")
        self.assertEqual(resolve_composition("auto", cfg), "auto")
        with self.assertRaises(GenError):
            resolve_composition("不存在的预设", cfg)


class TestTranslator(unittest.TestCase):
    def test_off_passthrough(self):
        result = translate_prompt("画一只柴犬", engine="off")
        self.assertEqual(result["engine_used"], "off")
        self.assertEqual(result["rewritten"], "画一只柴犬")


class TestCharacters(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config()

    def test_exact_match(self):
        self.assertEqual(characters.detect_character(self.cfg, "画一张洛天依 V4 公式服全身"), "洛天依")
        self.assertEqual(characters.detect_character(self.cfg, "洛天依-V4公式服 演唱会"), "洛天依")

    def test_style_not_a_mention(self):
        self.assertIsNone(characters.detect_character(self.cfg, "洛天依风格原创角色"))
        self.assertIsNone(characters.detect_character(self.cfg, "洛天依风 的画法"))

    def test_other_subject_no_injection(self):
        self.assertIsNone(characters.detect_character(self.cfg, "画一只柴犬晒太阳"))
        self.assertIsNone(characters.detect_character(self.cfg, "城市夜景"))

    def test_manual_fallback(self):
        result = characters.resolve_character(self.cfg, "随便什么", manual="洛天依")
        self.assertTrue(result["used"])
        self.assertIn("禁忌", result["desc"])

    def test_missing_character_hint(self):
        result = characters.resolve_character(self.cfg, "画一个角色", manual="不存在的人")
        self.assertFalse(result["used"])
        self.assertIn("角色表里没有", result["warning"])

    def test_reference_missing_degrade(self):
        with self.assertRaises(GenError):
            characters.load_character_reference(r"C:\不存在\的图.png", 100, 100)

    def test_reference_fit(self):
        png = make_png_bytes(40, 80)
        with tempfile.TemporaryDirectory() as tmp:
            ref = Path(tmp) / "ref.png"
            ref.write_bytes(png)
            data, mime, name = characters.load_character_reference(str(ref), 128, 128)
            self.assertEqual(probe_image_size(data), (128, 128))


class TestGenerateFlow(unittest.TestCase):
    def _fake_gen(self, cfg, prompt, width, height, model, **kwargs):
        return make_png_bytes(width, height)

    def _fake_img2img(self, cfg, prompt, width, height, model, image_bytes, mime, name, **kwargs):
        return make_png_bytes(width, height)

    def test_generate_character_and_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_dir = Path(tmp) / "out"
            mirror_dir = Path(tmp) / "mirror"
            cfg = load_config()
            cfg["save_dir"] = str(save_dir)
            cfg["mirror_dir"] = str(mirror_dir)
            with (
                mock.patch.object(generate, "load_config", return_value=cfg),
                mock.patch.object(generate, "gen_vertex_canvas_first", side_effect=self._fake_gen),
                mock.patch.object(generate, "gen_vertex", side_effect=self._fake_gen),
            ):
                result = generate.generate(
                    "画一张洛天依 V4 公式服全身",
                    size="768x1408",
                    seed=123,
                    translator="off",
                    composition="full-body",
                )
            self.assertTrue(result["ok"])
            self.assertTrue(Path(result["path"]).is_file())
            self.assertEqual(result["seed"], 123)
            self.assertTrue(result["character"]["used"])
            self.assertIn("角色设定，必须严格遵守", result["prompt_used"])
            self.assertTrue(result["size_check"]["match"])
            self.assertIn("画布优先", " ".join(result.get("warnings") or []))
            self.assertTrue((mirror_dir / Path(result["path"]).name).is_file())

    def test_generate_with_character_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_dir = Path(tmp) / "out"
            ref = Path(tmp) / "char.png"
            ref.write_bytes(make_png_bytes(50, 50))
            cfg = load_config()
            cfg["save_dir"] = str(save_dir)
            with (
                mock.patch.object(generate, "load_config", return_value=cfg),
                mock.patch.object(generate, "gen_vertex_img2img", side_effect=self._fake_img2img),
            ):
                result = generate.generate(
                    "洛天依 演唱会全身",
                    size="768x1408",
                    seed=7,
                    translator="off",
                    character_image=str(ref),
                )
            self.assertTrue(result["character"]["reference"])
            self.assertEqual(result["init_image"], str(ref))
            self.assertIn("保持图中人物设定", result["prompt_used"])

    def test_generate_character_reference_degrade(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = load_config()
            cfg["save_dir"] = str(Path(tmp) / "out")
            with (
                mock.patch.object(generate, "load_config", return_value=cfg),
                mock.patch.object(generate, "gen_vertex_canvas_first", side_effect=self._fake_gen),
            ):
                result = generate.generate(
                    "洛天依 舞台",
                    size="1024x1024",
                    seed=1,
                    translator="off",
                    character_image=r"C:\不存在\的角色图.png",
                )
            self.assertTrue(result["ok"])
            self.assertFalse(result["character"]["reference"])
            self.assertNotIn("init_image", result)
            self.assertTrue(any("参考图读取失败" in w for w in result.get("warnings") or []))

    def test_generate_manual_missing_character(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = load_config()
            cfg["save_dir"] = str(Path(tmp) / "out")
            with (
                mock.patch.object(generate, "load_config", return_value=cfg),
                mock.patch.object(generate, "gen_vertex_canvas_first", side_effect=self._fake_gen),
            ):
                result = generate.generate(
                    "画一个原创角色", seed=2, translator="off", character="不存在的人"
                )
            self.assertTrue(result["ok"])
            self.assertFalse(result["character"]["used"])
            self.assertTrue(any("角色表里没有" in w for w in result.get("warnings") or []))


class TestOutputPaths(unittest.TestCase):
    def test_save_dir_and_mirror(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_dir = Path(tmp) / "out"
            mirror_dir = Path(tmp) / "mirror"
            cfg = {"save_dir": str(save_dir), "mirror_dir": str(mirror_dir)}
            path = default_output_path("测试提示词", 42, cfg, ext="png")
            self.assertTrue(str(path).startswith(str(save_dir)))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(make_png_bytes())
            mirrored = mirror_output(str(path), cfg)
            self.assertTrue(mirrored)
            self.assertTrue(Path(mirrored).is_file())


class TestCli(unittest.TestCase):
    def test_config_json_output(self):
        from imagegen.cli import main

        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            code = main(["config", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(buf.getvalue())
        self.assertIn("config_file", data)
        self.assertIn("characters", data["config"])


class TestLibraryStats(unittest.TestCase):
    def test_stats_on_dryrun_db(self):
        try:
            from imagegen import library
        except Exception:  # noqa: BLE001
            self.skipTest("library 导入失败")
        pl = library.load_config()
        pl["mysql"]["db"] = "prompt_library_dryrun"
        try:
            st = library.stats(pl)
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"演练库不可用：{exc}")
        self.assertEqual(st["active"] + st["archived"], st["total"])
        self.assertEqual(st["archived"], 2000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
