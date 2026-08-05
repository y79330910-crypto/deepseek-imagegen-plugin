#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepSeek ImageGen v1.0.0 冒烟测试（单文件）。

覆盖：配置合并与密钥打码、尺寸工具、模型挑选、构图预设、翻译官 off、
参考图适配与降级、出图编排（模拟后端）、输出路径与镜像副本、
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

from imagegen import generate, reference  # noqa: E402
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
from imagegen.vertex import (  # noqa: E402
    OPENAI_IMAGE_SIZES,
    extra_backend_sizes,
    extra_size_whitelist,
    normalize_extra_size,
    parse_models_list,
    pick_best_image_model,
    pick_best_text_model,
)


def make_png_bytes(width: int = 64, height: int = 64) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (120, 180, 240)).save(buf, format="PNG")
    return buf.getvalue()


class TestConfig(unittest.TestCase):
    def test_merge_and_mask(self):
        cfg = load_config()
        self.assertNotIn("characters", cfg)
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

    def test_parse_models_list_v1_strings(self):
        self.assertEqual(
            parse_models_list(
                {"models": ["gemini-3-pro-image", "gemini-2.5-flash"], "alias_map": {}}
            ),
            ["gemini-3-pro-image", "gemini-2.5-flash"],
        )

    def test_parse_models_list_v2_objects(self):
        self.assertEqual(
            parse_models_list(
                {
                    "version": 2,
                    "models": [
                        {"id": "gemini-3-pro-image", "enabled": True},
                        {"id": "gemini-3.1-flash-image", "enabled": False},
                        {"id": "gemini-2.5-flash", "enabled": True},
                    ],
                }
            ),
            ["gemini-3-pro-image", "gemini-2.5-flash"],
        )


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


class TestReferencePrompts(unittest.TestCase):
    def test_template_order_all_types(self):
        for ref_type in reference.REFERENCE_TEMPLATES:
            brief = reference.build_reference_brief(ref_type, "画一张图")
            keep = brief.find("第1段·保持")
            change = brief.find("第2段·改变")
            scene = brief.find("第3段·场景")
            self.assertTrue(0 <= keep < change < scene, ref_type)
            label = reference.REF_TYPE_LABELS.get(
                ref_type, reference.REF_TYPE_LABELS["generic"]
            )
            self.assertIn(label, brief)
            self.assertLess(keep, change)
            self.assertLess(change, scene)

    def test_detect_avoid_items(self):
        self.assertIn("耳机", reference.detect_avoid_items("不要耳机，场景改为樱花公园"))
        items = reference.detect_avoid_items("把耳机上的圆点去掉")
        self.assertTrue(any("圆点" in item for item in items))
        self.assertIn("描点", reference.detect_avoid_items("去除描点"))
        self.assertEqual(
            reference.detect_avoid_items("不要耳机，不要出现任何耳机"),
            ["耳机"],
        )
        self.assertEqual(reference.detect_avoid_items("画一张春天的公园"), [])

    def test_suffix_contains_avoid(self):
        suffix = reference.build_reference_suffix("character", ["耳机"])
        self.assertIn("用户划除（不作为保留项）：耳机", suffix)
        self.assertIn("第1段·保持", suffix)
        self.assertIn("不保留（场景锚点", suffix)

    def test_brief_with_identity_list(self):
        brief = reference.build_reference_brief(
            "character",
            "不要耳机",
            identity_list="银灰双马尾；八字环发髻；黄色领带",
        )
        self.assertIn("身份锚点清单（必须逐项保留）", brief)
        self.assertIn("银灰双马尾；八字环发髻；黄色领带", brief)
        self.assertIn("不保留（场景锚点", brief)
        self.assertIn("不作为保留项", brief)

    def test_ref_type_manual_and_fallback(self):
        self.assertEqual(
            reference.resolve_ref_type("character", ""),
            ("character", "manual"),
        )
        self.assertEqual(
            reference.resolve_ref_type("", "画风景"),
            ("generic", "fallback"),
        )
        classify = {"ok": True, "type": "style", "preserve": "水彩质感"}
        self.assertEqual(
            reference.resolve_ref_type("", "画图", classify),
            ("style", "vision"),
        )

    def test_classify_disabled_and_missing(self):
        cfg = load_config()
        cfg["reference"] = {"auto_classify": False, "vision_script": ""}
        result = reference.classify_reference("x.png", cfg)
        self.assertFalse(result["ok"])
        self.assertEqual(result["method"], "disabled")
        cfg["reference"] = {
            "auto_classify": True,
            "vision_script": r"C:\不存在的目录\vision_bridge.py",
        }
        result = reference.classify_reference("x.png", cfg)
        self.assertFalse(result["ok"])
        self.assertEqual(result["method"], "fallback")

    def test_cli_accepts_multi_image(self):
        from imagegen.cli import build_parser

        args = build_parser().parse_args(
            ["generate", "测试", "--image", "a.png", "--image", "b.png",
             "--ref-role", "character", "--ref-role", "outfit", "--json"]
        )
        self.assertEqual(len(args.image), 2)
        self.assertEqual(args.ref_role, ["character", "outfit"])


class TestGenerateFlow(unittest.TestCase):
    def _fake_gen(self, cfg, prompt, width, height, model, **kwargs):
        return make_png_bytes(width, height)

    def _fake_img2img(self, cfg, prompt, width, height, model, images, **kwargs):
        return make_png_bytes(width, height)

    def test_generate_and_save(self):
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
                mock.patch.object(generate, "gen_vertex_img2img", side_effect=self._fake_img2img),
            ):
                result = generate.generate(
                    "画一张湖边公园春日场景",
                    size="768x1408",
                    seed=123,
                    translator="off",
                    composition="full-body",
                )
            self.assertTrue(result["ok"])
            self.assertTrue(Path(result["path"]).is_file())
            self.assertEqual(result["seed"], 123)
            self.assertNotIn("角色设定", result["prompt_used"])
            self.assertTrue(result["size_check"]["match"])
            self.assertIn("画布优先", " ".join(result.get("warnings") or []))
            self.assertTrue((mirror_dir / Path(result["path"]).name).is_file())

    def test_generate_user_reference_three_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_dir = Path(tmp) / "out"
            ref = Path(tmp) / "ref.png"
            ref.write_bytes(make_png_bytes(50, 50))
            cfg = load_config()
            cfg["save_dir"] = str(save_dir)
            cfg["reference"] = {"auto_classify": False, "vision_script": "", "classify_timeout": 90}
            with (
                mock.patch.object(generate, "load_config", return_value=cfg),
                mock.patch.object(generate, "gen_vertex_img2img", side_effect=self._fake_img2img),
            ):
                result = generate.generate(
                    "保持参考图中的角色不变；不要耳机；场景改为春日樱花公园",
                    size="1024x1024",
                    seed=9,
                    translator="off",
                    init_images=[str(ref)],
                    ref_roles=["character"],
                )
            self.assertTrue(result["ok"])
            self.assertEqual(result["reference"]["type"], "character")
            self.assertEqual(result["reference"]["method"], "manual")
            self.assertIn("耳机", result["reference"]["avoid"])
            self.assertIn("参考图硬性要求", result["prompt_used"])
            self.assertIn("用户划除（不作为保留项）：耳机", result["prompt_used"])
            self.assertIn("第1段·保持", result["prompt_used"])
            self.assertIn("不保留（场景锚点", result["prompt_used"])

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


class TestMultiReference(unittest.TestCase):
    def test_multi_brief_has_role_isolation(self):
        items = [
            {"path": "a.png", "type": "character", "label": "角色人物", "method": "manual",
             "preserve": "蓝色长发，红瞳"},
            {"path": "b.png", "type": "outfit", "label": "服装造型", "method": "manual",
             "preserve": "白色连衣裙"},
        ]
        brief = reference.build_multi_reference_brief(items, ["耳机"])
        self.assertIn("图1（角色人物）", brief)
        self.assertIn("图2（服装造型）", brief)
        self.assertIn("互不借用", brief)
        self.assertIn("蓝色长发，红瞳", brief)
        self.assertIn("耳机", brief)

    def test_generate_multi_ref_mocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_dir = Path(tmp) / "out"
            ref1 = Path(tmp) / "ref1.png"
            ref1.write_bytes(make_png_bytes(64, 64))
            ref2 = Path(tmp) / "ref2.png"
            ref2.write_bytes(make_png_bytes(64, 64))
            cfg = load_config()
            cfg["save_dir"] = str(save_dir)
            cfg["reference"] = {"auto_classify": False, "vision_script": "", "classify_timeout": 90}
            captured: dict = {}
            def fake_img2img(cfg, prompt, width, height, model, images, **kwargs):
                captured["images"] = images
                return make_png_bytes(width, height)
            with (
                mock.patch.object(generate, "load_config", return_value=cfg),
                mock.patch.object(generate, "gen_vertex_img2img", side_effect=fake_img2img),
            ):
                result = generate.generate(
                    "保持角色不变，穿上第二张图的服装，场景全新",
                    size="1024x1024",
                    seed=7,
                    translator="off",
                    init_images=[str(ref1), str(ref2)],
                    ref_roles=["character", "outfit"],
                )
            self.assertTrue(result["ok"])
            self.assertEqual(result["reference"]["method"], "multi")
            self.assertEqual(len(result["reference"]["items"]), 2)
            self.assertEqual(
                [it["type"] for it in result["reference"]["items"]],
                ["character", "outfit"],
            )
            self.assertEqual(len(captured["images"]), 2)
            self.assertIn("图1（角色人物）", result["prompt_used"])
            self.assertIn("图2（服装造型）", result["prompt_used"])
            self.assertIn("互不借用", result["prompt_used"])

    def test_max_refs_rejected(self):
        cfg = load_config()
        with mock.patch.object(generate, "load_config", return_value=cfg):
            with self.assertRaises(GenError):
                generate.generate(
                    "测试", translator="off",
                    init_images=["a.png", "b.png", "c.png", "d.png", "e.png"],
                )


class TestExtraBackend(unittest.TestCase):
    def test_whitelist_presets(self):
        self.assertEqual(
            extra_size_whitelist({"model": "gpt-image-2-4k超分"}),
            ["2048x2048", "2560x1440", "3840x2160", "2160x3840", "3696x1584"],
        )
        self.assertEqual(
            extra_size_whitelist({"model": "gpt-image-2-原生4k"}),
            ["2048x2048", "3840x2160", "2160x3840"],
        )
        self.assertEqual(extra_size_whitelist({"model": "gpt-image-2"}), OPENAI_IMAGE_SIZES)
        self.assertEqual(
            extra_size_whitelist({"model": "gpt-image-2", "sizes": "1024x1024, 2048x2048"}),
            ["1024x1024", "2048x2048"],
        )

    def test_normalize_whitelist(self):
        self.assertEqual(normalize_extra_size(3840, 2160, ["2048x2048", "3840x2160"]), "3840x2160")
        self.assertEqual(normalize_extra_size(1024, 1024, ["2048x2048", "3840x2160"]), "2048x2048")
        self.assertEqual(normalize_extra_size(768, 1408, None), "1024x1536")
        self.assertEqual(normalize_extra_size(2000, 1000, None), "1536x1024")

    def test_model_override_whitelist(self):
        cfg = {
            "extra_backends": {
                "dragtokens": {
                    "base_url": "https://x",
                    "api_key": "sk-test",
                    "model": "gpt-image-2",
                }
            }
        }
        self.assertEqual(extra_backend_sizes(cfg, "dragtokens", "gpt-image-2-原生4k"), ["2048x2048", "3840x2160", "2160x3840"])
        self.assertEqual(extra_backend_sizes(cfg, "dragtokens", ""), OPENAI_IMAGE_SIZES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
