#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v0.7 专项离线测试：尺寸兼容层 / 构图预设 / 检查清单 / 修复分类 / 空数据重试。"""

from __future__ import annotations

import base64
import json
import sys
import tempfile
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

import image_gen  # noqa: E402


FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}" + (f"  ({detail})" if detail else ""))
    if not condition:
        FAILURES.append(name)


def make_png_bytes(width: int, height: int) -> bytes:
    from PIL import Image

    img = Image.new("RGB", (width, height), (200, 200, 200))
    buf = tempfile.SpooledTemporaryFile()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf.read()


def test_size_utils() -> None:
    check(
        "sizes_match 像素一致",
        image_gen.sizes_match((768, 1408), (768, 1408))["ok"],
    )
    check(
        "sizes_match 同画幅不同分辨率",
        image_gen.sizes_match((1024, 1536), (1696, 2528))["ok"],
    )
    check(
        "sizes_match 方向相反拒绝",
        not image_gen.sizes_match((1024, 1536), (1408, 768))["ok"],
    )
    check(
        "sizes_match 无法读取返回 False",
        not image_gen.sizes_match((1024, 1024), None)["ok"],
    )
    check("aspect_ratio_key 竖版归 9:16", image_gen.aspect_ratio_key(768, 1408) == (9, 16))
    check("aspect_ratio_key 横版归 16:9", image_gen.aspect_ratio_key(1408, 768) == (16, 9))
    check("aspect_ratio_key 2:3", image_gen.aspect_ratio_key(1024, 1536) == (2, 3))
    check("aspect_ratio_key 1:1", image_gen.aspect_ratio_key(1024, 1024) == (1, 1))
    check("canvas_size_for 竖版", image_gen.canvas_size_for(1024, 1536) == (768, 1408))
    check("canvas_size_for 横版", image_gen.canvas_size_for(1408, 768) == (1408, 768))
    check("canvas_size_for 方形", image_gen.canvas_size_for(1024, 1024) == (1024, 1024))


def test_composition_preset() -> None:
    cfg = image_gen.load_config()
    check("resolve 全身别名", image_gen.resolve_composition("全身", cfg) == "full-body")
    check("resolve 英文", image_gen.resolve_composition("full-body", cfg) == "full-body")
    preset = image_gen.get_composition_preset("full-body", cfg)
    check("全身预设画幅", preset.get("size") == "768x1408")
    check("全身预设清单", len(image_gen.composition_checklist("full-body", cfg)) >= 3)
    check(
        "全身清单含脚入画",
        any("双脚" in x or "脚" in x for x in image_gen.composition_checklist("full-body", cfg)),
    )
    check(
        "构图提示词含全身",
        "全身" in image_gen.composition_prompt_suffix("full-body", cfg),
    )
    try:
        image_gen.resolve_composition("不存在", cfg)
        check("未知预设报错", False)
    except image_gen.GenError:
        check("未知预设报错", True)


def test_fix_instruction_classify() -> None:
    detail = image_gen.build_fix_instruction("1. 缺阴影", keep_layout=True)
    check("细节修正保持布局", "保持整体布局" in detail)
    comp = image_gen.build_fix_instruction("1. 双脚被裁掉", keep_layout=False)
    check("构图修正不含保持布局", "保持整体布局" not in comp)
    check("构图修正要求调整取景", "取景" in comp)
    check("构图修正保留人物设定", "人物设定" in comp)


def test_empty_data_retry() -> None:
    empty_body = json.dumps({"created": 1, "data": []}).encode("utf-8")
    try:
        image_gen._extract_image_from_response(empty_body)
        check("空 data 抛 EmptyImageError", False)
    except image_gen.EmptyImageError:
        check("空 data 抛 EmptyImageError", True)
    # _gen_openai_image：前两次空数据，第三次成功
    calls: list[str] = []
    png = make_png_bytes(64, 64)

    def fake_http(url: str, **kwargs):
        calls.append(url)
        if len(calls) < 3:
            return 200, empty_body, "application/json"
        b64 = base64.b64encode(png).decode("ascii")
        return 200, json.dumps({"data": [{"b64_json": b64}]}).encode("utf-8"), "application/json"

    orig = image_gen._http
    image_gen._http = fake_http
    try:
        result = image_gen._gen_openai_image(
            "http://x/v1", "key", "model", "prompt", 512, 512,
            empty_retries=2, retry_delay_base=0.01,
        )
    finally:
        image_gen._http = orig
    check("空数据自动重试 3 次", len(calls) == 3)
    check("重试后拿到图片", result == png)
    # 全部空数据时最终报错
    calls2: list[str] = []

    def fake_http_all_empty(url: str, **kwargs):
        calls2.append(url)
        return 200, empty_body, "application/json"

    image_gen._http = fake_http_all_empty
    try:
        image_gen._gen_openai_image(
            "http://x/v1", "key", "model", "prompt", 512, 512,
            empty_retries=1, retry_delay_base=0.01,
        )
        check("全部空数据最终报错", False)
    except image_gen.EmptyImageError:
        check("全部空数据最终报错", True)
    finally:
        image_gen._http = orig


def test_checklist_parsing() -> None:
    text = (
        "1. 双脚完整入画：不通过：脚被画面底部裁掉\n"
        "2. 头顶留白：通过\n"
        "3. 全身从头到脚完整：不通过：只有半身\n"
        "4. 非Q版人体比例：通过\n"
        "人物问题：\n无\n背景问题：\n无"
    )
    result = image_gen._finalize_vision_check(
        text, tiered=True, checklist=["双脚完整入画", "头顶留白", "全身从头到脚完整", "非Q版人体比例"]
    )
    check("清单逐项解析", result.get("checklist_passed") == 2 and result.get("checklist_failed") == 2)
    check("存在构图问题", result.get("has_composition_issues") is True)
    check("构图问题含脚", any("双脚" in x for x in result.get("composition_issues") or []))
    check("清单行不计入普通问题", result.get("issue_counts", {}).get("character", 0) == 0)
    check("issue_counts 含 composition", result.get("issue_counts", {}).get("composition", 0) == 2)


def test_checklist_paraphrase() -> None:
    """视觉模型换措辞（真实输出）也能正确判定，且清单行不污染人物/背景问题。"""
    text = (
        "1. 通过：人物双脚及白色长靴完整展示在画面底部，未被裁切。\n"
        "2. 通过：头顶上方留有充足的空间，展示了上方聚光灯束与烟雾。\n"
        "3. 通过：人物从发饰、头顶到脚尖鞋底全部完整入画。\n"
        "4. 通过：人体比例为正常的七头身少女动漫比例，非Q版或大头造型。\n"
        "人物问题：\n1. 动作左右颠倒\n背景问题：\n无"
    )
    result = image_gen._finalize_vision_check(
        text,
        tiered=True,
        checklist=["双脚完整入画", "头顶留白", "全身从头到脚完整", "非Q版人体比例"],
    )
    check("换措辞也判定通过", result.get("checklist_failed") == 0)
    check("换措辞时无构图问题", result.get("has_composition_issues") is False)
    check("人物问题仍保留", result.get("issue_counts", {}).get("character", 0) == 1)
    check("清单行不污染背景", "构图清单" not in str(result.get("background_issues") or ""))
    # 只回“通过/不通过”时按行号对应
    text2 = "【构图清单】\n1. 通过\n2. 通过\n3. 通过\n4. 不通过\n人物问题：\n无\n背景问题：\n无"
    result2 = image_gen._finalize_vision_check(
        text2,
        tiered=True,
        checklist=["双脚完整入画", "头顶留白", "全身从头到脚完整", "非Q版人体比例"],
    )
    check("按行号对应", result2.get("checklist_passed") == 3 and result2.get("checklist_failed") == 1)
    check("按行号判定出构图问题", result2.get("has_composition_issues") is True)


def test_fix_accepted_weighted() -> None:
    old = {
        "ok": True,
        "has_issues": True,
        "issues": "人物问题：\n无\n背景问题：\n无",
        "checklist": {"a": {"pass": False}},
        "checklist_passed": 0,
        "checklist_failed": 2,
        "has_composition_issues": True,
        "issue_counts": {"character": 0, "background": 0, "composition": 2},
    }
    new = {
        "ok": True,
        "has_issues": True,
        "issues": "人物问题：\n无\n背景问题：\n无",
        "checklist": {"a": {"pass": True}},
        "checklist_passed": 4,
        "checklist_failed": 0,
        "has_composition_issues": False,
        "issue_counts": {"character": 0, "background": 0, "composition": 0},
    }
    accepted, why = image_gen._fix_accepted(old, new)
    check("清单改善则保留", accepted, why)
    accepted2, why2 = image_gen._fix_accepted(old, new, new_size_ok=False)
    check("尺寸不符直接判失败", not accepted2, why2)
    # 新增人物错误即使清单改善也拒绝
    new_bad = dict(new)
    new_bad["issue_counts"] = {"character": 2, "background": 0, "composition": 0}
    new_bad["has_character_issues"] = True
    accepted3, why3 = image_gen._fix_accepted(old, new_bad)
    check("引入人物错误拒绝", not accepted3, why3)


def test_probe_ext_pillow() -> None:
    png = make_png_bytes(321, 123)
    check("Pillow 探测 PNG", image_gen.probe_image_size_ext(png) == (321, 123))
    check("垃圾数据返回 None", image_gen.probe_image_size_ext(b"garbage") is None)


def test_generate_image_size_compat() -> None:
    """模拟：竖版请求直接走画布优先；横版文生图返回错误方向时自动画布兜底。"""
    landscape = make_png_bytes(1408, 768)
    portrait = make_png_bytes(768, 1408)
    orig_gen = image_gen.gen_vertex
    orig_canvas = image_gen.gen_vertex_canvas_first
    orig_mirror = image_gen.mirror_output
    image_gen.gen_vertex = lambda *a, **k: (_ for _ in ()).throw(
        image_gen.GenError("竖版不应走文生图")
    )
    image_gen.gen_vertex_canvas_first = lambda *a, **k: portrait
    image_gen.mirror_output = lambda *a, **k: None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            result = image_gen.generate_image("竖版测试", backend="vertex", size="768x1408", out=tmp)
    finally:
        image_gen.gen_vertex = orig_gen
        image_gen.gen_vertex_canvas_first = orig_canvas
        image_gen.mirror_output = orig_mirror
    check("竖版直接走画布优先", result.get("size_check", {}).get("canvas_first") is True)
    check("actual_size 如实上报", result.get("actual_size") == "768x1408")
    check("size_check match", result.get("size_check", {}).get("match") is True)

    # 横版：文生图返回了竖版（方向错），触发兜底
    calls: list[str] = []
    image_gen.gen_vertex = lambda *a, **k: (calls.append("gen") or portrait)
    image_gen.gen_vertex_canvas_first = lambda *a, **k: (calls.append("canvas") or landscape)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            result2 = image_gen.generate_image(
                "横版测试", backend="vertex", size="1408x768", out=tmp
            )
    finally:
        image_gen.gen_vertex = orig_gen
        image_gen.gen_vertex_canvas_first = orig_canvas
        image_gen.mirror_output = orig_mirror
    check("横版先试文生图", calls and calls[0] == "gen")
    check("方向错时画布兜底", "canvas" in calls)
    check("兜底后画幅匹配", result2.get("size_check", {}).get("match") is True)


def test_generate_image_composition_default_size() -> None:
    png = make_png_bytes(768, 1408)
    orig_canvas = image_gen.gen_vertex_canvas_first
    orig_mirror = image_gen.mirror_output
    seen: dict = {}

    def fake_canvas(cfg, prompt, width, height, model, **kwargs):
        seen["size"] = (width, height)
        return png

    image_gen.gen_vertex_canvas_first = fake_canvas
    image_gen.mirror_output = lambda *a, **k: None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            result = image_gen.generate_image(
                "全身测试", backend="vertex", composition="full-body", out=tmp
            )
    finally:
        image_gen.gen_vertex_canvas_first = orig_canvas
        image_gen.mirror_output = orig_mirror
    check("预设默认画幅 768x1408", seen.get("size") == (768, 1408))
    check("结果尺寸如实", result.get("actual_size") == "768x1408")
    check("构图预设记录", result.get("composition") == "full-body")


def test_backend_fallback() -> None:
    """主后端失败时按 --fallback-backends 降级，并如实记录。"""
    png = make_png_bytes(1024, 1024)
    orig_vertex = image_gen.gen_vertex
    orig_canvas = image_gen.gen_vertex_canvas_first
    orig_poll = image_gen.gen_pollinations
    orig_mirror = image_gen.mirror_output
    image_gen.gen_vertex = lambda *a, **k: (_ for _ in ()).throw(
        image_gen.GenError("模拟代理故障")
    )
    image_gen.gen_vertex_canvas_first = lambda *a, **k: (_ for _ in ()).throw(
        image_gen.GenError("模拟画布兜底故障")
    )
    image_gen.gen_pollinations = lambda *a, **k: (png, "image/png")
    image_gen.mirror_output = lambda *a, **k: None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            result = image_gen.generate_image(
                "兜底测试", backend="vertex", size="1024x1024",
                fallback_backends=["pollinations"], out=tmp,
            )
    finally:
        image_gen.gen_vertex = orig_vertex
        image_gen.gen_vertex_canvas_first = orig_canvas
        image_gen.gen_pollinations = orig_poll
        image_gen.mirror_output = orig_mirror
    check("降级到 pollinations", result.get("backend_used") == "pollinations")
    check("降级有警告", any("备用后端" in str(w) for w in result.get("warnings") or []))
    check("降级有尝试记录", len(result.get("backend_attempts") or []) == 2)
    check("降级结果真实尺寸", result.get("actual_size") == "1024x1024")


def test_save_probe_cache() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        fake_file = Path(tmp) / "config.json"
        fake_file.write_text(
            json.dumps({"default_backend": "vertex", "size_policy": {"mode": "auto"}}),
            encoding="utf-8",
        )
        old = image_gen.CONFIG_FILE
        image_gen.CONFIG_FILE = fake_file
        try:
            path = image_gen.save_probe_cache(
                "vertex",
                [{"requested": "768x1408", "verdict": "需画布优先"}],
            )
            saved = json.loads(fake_file.read_text(encoding="utf-8"))
            cache = saved["size_policy"]["probe_cache"]["vertex"]
            check("探针缓存写入", cache["probes"][0]["requested"] == "768x1408")
            check("缓存不破坏其他配置", saved["default_backend"] == "vertex")
            check("返回缓存路径", str(fake_file) == path)
        finally:
            image_gen.CONFIG_FILE = old


def main() -> int:
    print("=== v0.7 专项测试 ===")
    test_size_utils()
    test_composition_preset()
    test_fix_instruction_classify()
    test_empty_data_retry()
    test_checklist_parsing()
    test_checklist_paraphrase()
    test_fix_accepted_weighted()
    test_probe_ext_pillow()
    test_generate_image_size_compat()
    test_generate_image_composition_default_size()
    test_backend_fallback()
    test_save_probe_cache()
    print(f"\n通过 {11 - len(FAILURES)}/11 组，失败 {len(FAILURES)} 组")
    if FAILURES:
        print("失败项：" + "、".join(FAILURES))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
