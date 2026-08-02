#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线冒烟测试：不依赖网络，验证 image_gen.py 的核心逻辑。"""

from __future__ import annotations

import json
import base64
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


def test_parse_size() -> None:
    check("parse_size 1024x1024", image_gen.parse_size("1024x1024") == (1024, 1024))
    check("parse_size 1536x1024", image_gen.parse_size("1536x1024") == (1536, 1024))
    try:
        image_gen.parse_size("1024")
        check("parse_size 拒绝非法格式", False)
    except image_gen.GenError:
        check("parse_size 拒绝非法格式", True)


def test_slugify() -> None:
    check("slugify 中文", image_gen.slugify("一只柴犬 宇航员") == "一只柴犬-宇航员")
    check("slugify 英文", image_gen.slugify("Hello World!") == "Hello-World")
    check("slugify 空串回退", image_gen.slugify("!!!") == "image")


def test_config_merge() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        fake = Path(tmp) / "config.json"
        fake.write_text(
            json.dumps({"default_backend": "siliconflow", "siliconflow": {"api_key": "sk-test"}}),
            encoding="utf-8",
        )
        old = image_gen.CONFIG_FILE
        image_gen.CONFIG_FILE = fake
        try:
            cfg = image_gen.load_config()
            check("config 合并默认值", cfg["default_backend"] == "siliconflow")
            check("config 嵌套覆盖", cfg["siliconflow"]["api_key"] == "sk-test")
            check(
                "config 保留未覆盖字段",
                cfg["siliconflow"]["base_url"].startswith("https://api.siliconflow.cn"),
            )
            check(
                "config 保留其他后端",
                cfg["sd_webui"]["base_url"].startswith("http://127.0.0.1"),
            )
        finally:
            image_gen.CONFIG_FILE = old


def test_pollinations_url() -> None:
    cfg = image_gen.load_config()
    captured: dict = {}

    def fake_http(url: str, **kwargs):
        captured["url"] = url
        return 200, b"fake-png", "image/png"

    original = image_gen._http
    image_gen._http = fake_http
    try:
        image_gen.gen_pollinations(cfg, "a cat & dog 中文", 512, 512, 42, "")
    finally:
        image_gen._http = original
    url = captured.get("url", "")
    check("pollinations URL 编码提示词", "/a%20cat%20%26%20dog%20%E4%B8%AD%E6%96%87" in url)
    check("pollinations URL 含尺寸", "width=512" in url and "height=512" in url)
    check("pollinations URL 含种子", "seed=42" in url)
    check("pollinations URL 去水印", "nologo=true" in url)


def test_comfyui_workflow() -> None:
    workflow = image_gen._comfyui_workflow(
        "sd_xl_base_1.0.safetensors",
        "hello",
        "bad",
        1024,
        1024,
        7,
        28,
        7.0,
        "euler",
        "normal",
    )
    check("comfyui 工作流包含提示词", workflow["6"]["inputs"]["text"] == "hello")
    check("comfyui 工作流包含负面提示词", workflow["7"]["inputs"]["text"] == "bad")
    check(
        "comfyui 工作流包含 checkpoint",
        workflow["4"]["inputs"]["ckpt_name"] == "sd_xl_base_1.0.safetensors",
    )
    check("comfyui 工作流包含尺寸", workflow["5"]["inputs"]["width"] == 1024)


def test_comfyui_img2img_workflow() -> None:
    workflow = image_gen._comfyui_workflow(
        "sd_xl_base_1.0.safetensors",
        "hello",
        "bad",
        1024,
        1024,
        7,
        28,
        7.0,
        "euler",
        "normal",
        init_image="my_photo.png",
        denoise=0.6,
    )
    check("comfyui 图生图 LoadImage", workflow["10"]["class_type"] == "LoadImage")
    check(
        "comfyui 图生图文件名",
        workflow["10"]["inputs"]["image"] == "my_photo.png",
    )
    check("comfyui 图生图 VaeEncode", workflow["5"]["class_type"] == "VaeEncode")
    check(
        "comfyui 图生图 latent 来源",
        workflow["5"]["inputs"]["pixels"] == ["10", 0],
    )
    check("comfyui 图生图 denoise", workflow["3"]["inputs"]["denoise"] == 0.6)


def test_probe_image_size() -> None:
    png = (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + (640).to_bytes(4, "big")
        + (480).to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
        + b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    check("PNG 宽高解析", image_gen.probe_image_size(png) == (640, 480))
    jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x02\x00\x00\x01\x00\x01\x00\x00" + (
        b"\xff\xc0\x00\x11\x08\x01\x2c\x02\x80\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00"
    )
    check("JPEG 宽高解析", image_gen.probe_image_size(jpeg) == (640, 300))
    webp = (
        b"RIFF\x24\x00\x00\x00WEBPVP8X"
        + b"\x00" * 8  # chunk size(4) + flags(1) + reserved(3)
        + (639).to_bytes(3, "little")
        + (479).to_bytes(3, "little")
    )
    check("WebP 宽高解析", image_gen.probe_image_size(webp) == (640, 480))
    check("垃圾数据返回 None", image_gen.probe_image_size(b"not an image") is None)


def test_load_init_image_local() -> None:
    png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (64).to_bytes(4, "big") + (64).to_bytes(4, "big")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "photo.PNG"
        path.write_bytes(png)
        data, mime, name = image_gen.load_init_image(str(path))
        check("本地图片读取", data == png)
        check("MIME 嗅探", mime == "image/png")
        check("文件名保留", name == "photo.PNG")
        try:
            image_gen.load_init_image(str(Path(tmp) / "missing.png"))
            check("缺失文件报错", False)
        except image_gen.GenError:
            check("缺失文件报错", True)


def test_multipart() -> None:
    body, content_type = image_gen._multipart(
        {"model": "gemini-3-pro-image", "prompt": "hi", "n": "1", "size": "512x512"},
        [("image", "a.png", b"\x89PNG-data", "image/png")],
    )
    check("multipart 含字段", b'name="model"' in body and b'name="prompt"' in body)
    check("multipart 含文件", b'name="image"; filename="a.png"' in body)
    check("multipart 含图片内容", b"\x89PNG-data" in body)
    check("multipart Content-Type", content_type.startswith("multipart/form-data; boundary="))


def test_vertex_img2img() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg_dir = Path(tmp) / "config"
        cfg_dir.mkdir()
        (cfg_dir / "config.json").write_text(json.dumps({"port_api": 2199}), encoding="utf-8")
        (cfg_dir / "api_keys.txt").write_text("waqeq:sk-testkey123\n", encoding="utf-8")
        (cfg_dir / "models.json").write_text(
            json.dumps({"models": ["gemini-3-pro-image", "gemini-3.6-flash"]}), encoding="utf-8"
        )
        cfg = image_gen.load_config()
        cfg["vertex"] = {"dir": tmp, "base_url": "", "api_key": "", "model": ""}
        captured: dict = {}

        def fake_http(url: str, **kwargs):
            captured["url"] = url
            captured["headers"] = kwargs.get("headers", {})
            captured["raw_body"] = kwargs.get("raw_body")
            fake_png = base64.b64encode(b"fake-image-bytes").decode("ascii")
            return 200, json.dumps({"data": [{"b64_json": fake_png}]}).encode("utf-8"), "application/json"

        original = image_gen._http
        image_gen._http = fake_http
        try:
            data = image_gen.gen_vertex_img2img(
                cfg, "make it red", 512, 512, "", b"\x89PNG-raw", "image/png", "a.png"
            )
        finally:
            image_gen._http = original
        check("vertex 图生图走 /images/edits", captured.get("url", "").endswith("/images/edits"))
        check("vertex 图生图带鉴权", "Bearer sk-testkey123" in captured.get("headers", {}).get("Authorization", ""))
        check("vertex 图生图 multipart 请求体", b'name="image"; filename="a.png"' in captured.get("raw_body", b""))
        check("vertex 图生图返回图片", data == b"fake-image-bytes")


def test_sd_webui_img2img() -> None:
    cfg = image_gen.load_config()
    captured: dict = {}

    def fake_http(url: str, **kwargs):
        captured["url"] = url
        captured["payload"] = kwargs.get("payload")
        fake_png = base64.b64encode(b"fake-image-bytes").decode("ascii")
        return 200, json.dumps({"images": [fake_png]}).encode("utf-8"), "application/json"

    original = image_gen._http
    image_gen._http = fake_http
    try:
        data = image_gen.gen_sd_webui_img2img(
            cfg, "make it red", "bad", 512, 512, 42, 28, 7.0, "Euler a",
            (b"\x89PNG-raw", "image/png", "a.png"), 0.6,
        )
    finally:
        image_gen._http = original
    payload = captured.get("payload", {})
    check("sd-webui 图生图走 /img2img", captured.get("url", "").endswith("/sdapi/v1/img2img"))
    check("sd-webui init_images 为 base64", payload.get("init_images") == [base64.b64encode(b"\x89PNG-raw").decode("ascii")])
    check("sd-webui denoising_strength", payload.get("denoising_strength") == 0.6)
    check("sd-webui 返回图片", data == b"fake-image-bytes")


def test_comfyui_upload_image() -> None:
    cfg = image_gen.load_config()
    captured: dict = {}

    def fake_http(url: str, **kwargs):
        captured["url"] = url
        captured["raw_body"] = kwargs.get("raw_body")
        return 200, json.dumps({"name": "img2img.png", "subfolder": "", "type": "input"}).encode("utf-8"), "application/json"

    original = image_gen._http
    image_gen._http = fake_http
    try:
        name = image_gen.upload_comfyui_image(cfg, b"\x89PNG-raw", "photo.png", "image/png")
    finally:
        image_gen._http = original
    check("comfyui 上传走 /upload/image", captured.get("url", "").endswith("/upload/image"))
    check("comfyui 上传 multipart 含文件", b'name="image"; filename="deepseek-imagegen_' in captured.get("raw_body", b""))
    check("comfyui 上传返回文件名", name == "img2img.png")


def test_img2img_routing() -> None:
    png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (64).to_bytes(4, "big") + (64).to_bytes(4, "big")
    with tempfile.TemporaryDirectory() as tmp:
        ref = Path(tmp) / "ref.png"
        ref.write_bytes(png)
        out_dir = Path(tmp) / "out"

        cfg = image_gen.load_config()
        cfg["save_dir"] = str(out_dir)
        cfg["mirror_dir"] = ""
        cfg["default_backend"] = "sd-webui"
        original_load = image_gen.load_config
        image_gen.load_config = lambda: cfg  # type: ignore[method-assign]

        captured: dict = {}
        original = image_gen.gen_sd_webui_img2img

        def fake_gen_sd_img2img(c, p, n, w, h, s, st, cf, sm, init, den):
            captured.update({"w": w, "h": h, "den": den, "init": init})
            return b"fake-image-bytes"

        image_gen.gen_sd_webui_img2img = fake_gen_sd_img2img  # type: ignore[method-assign]
        try:
            result = image_gen.generate_image(
                "make it red", backend="sd-webui", init_image=str(ref)
            )
        finally:
            image_gen.gen_sd_webui_img2img = original
            image_gen.load_config = original_load  # type: ignore[method-assign]

        check("图生图自动沿用原图尺寸", captured.get("w") == 64 and captured.get("h") == 64)
        check("图生图默认去噪 0.6", captured.get("den") == 0.6)
        check("图生图结果含 init_image", result.get("init_image") == str(ref))
        check("图生图结果含 denoise", result.get("denoise") == 0.6)
        check("图生图输出文件存在", Path(result["path"]).exists())

        try:
            image_gen.generate_image("x", backend="pollinations", init_image=str(ref))
            check("pollinations 图生图拒绝", False)
        except image_gen.GenError:
            check("pollinations 图生图拒绝", True)


def test_resolve_backend() -> None:
    cfg = image_gen.load_config()
    check("resolve auto -> 默认", image_gen.resolve_backend("auto", cfg) == "vertex")
    check("resolve 别名", image_gen.resolve_backend("webui", cfg) == "sd-webui")
    check("resolve comfy", image_gen.resolve_backend("comfy", cfg) == "comfyui")
    check("resolve vertex 别名", image_gen.resolve_backend("vproxy", cfg) == "vertex")


def test_default_output_path() -> None:
    cfg = image_gen.load_config()
    with tempfile.TemporaryDirectory() as tmp:
        cfg["save_dir"] = tmp
        path = image_gen.default_output_path("测试 图片", 42, cfg)
        check(
            "默认输出文件名",
            path.name.startswith("deepseek-imagegen_") and path.name.endswith(".png"),
        )
        check("默认输出目录生效", str(path.parent) == str(Path(tmp).resolve()))


def test_mask_key() -> None:
    check("mask_key 未设置", image_gen.mask_key("") == "(未设置)")
    check("mask_key 打码", image_gen.mask_key("sk-abcdefghijkl") == "sk-a*******ijkl")


def test_read_first_api_key() -> None:
    text = "# 注释\nwaqeq:sk-aaaabbbb\n第三方:sk-ccccdddd\n"
    check("api_keys 解析 name:key", image_gen.read_first_api_key(text) == "sk-aaaabbbb")
    check("api_keys 解析裸 key", image_gen.read_first_api_key("sk-xxxx") == "sk-xxxx")
    check("api_keys 空返回", image_gen.read_first_api_key("# only comment") == "")


def test_pick_best_image_model() -> None:
    models = [
        "gemini-2.5-flash-image",
        "gemini-3.1-flash-image",
        "gemini-3.1-flash-image-preview",
        "gemini-3-pro-image",
        "gemini-3-pro-image-preview",
        "gemini-3.1-flash-lite-image",
        "gemini-2.5-flash",
        "gemini-3.6-flash",
    ]
    check("最佳图像模型", image_gen.pick_best_image_model(models) == "gemini-3-pro-image")
    check("无图像模型返回空", image_gen.pick_best_image_model(["gemini-3.6-flash"]) == "")


def test_discover_vertex() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg_dir = Path(tmp) / "config"
        cfg_dir.mkdir()
        (cfg_dir / "config.json").write_text(json.dumps({"port_api": 2199}), encoding="utf-8")
        (cfg_dir / "api_keys.txt").write_text("waqeq:sk-testkey123\n", encoding="utf-8")
        (cfg_dir / "models.json").write_text(
            json.dumps({"models": ["gemini-3-pro-image", "gemini-3.6-flash"]}), encoding="utf-8"
        )
        cfg = image_gen.load_config()
        cfg["vertex"] = {"dir": tmp, "base_url": "", "api_key": "", "model": ""}
        info = image_gen.discover_vertex(cfg)
        check("vertex 端口读取", info["port"] == 2199)
        check("vertex base_url", info["base_url"] == "http://127.0.0.1:2199/v1")
        check("vertex 密钥读取", info["api_key"] == "sk-testkey123")
        check("vertex 最佳模型", info["model"] == "gemini-3-pro-image")
        check("vertex 图像模型列表", info["image_models"] == ["gemini-3-pro-image"])


def test_ext_from_content_type() -> None:
    check("jpg 扩展名", image_gen.ext_from_content_type("image/jpeg") == "jpg")
    check("webp 扩展名", image_gen.ext_from_content_type("image/webp; charset=binary") == "webp")
    check("png 扩展名", image_gen.ext_from_content_type("image/png") == "png")


def test_mirror_output() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "img.png"
        src.write_bytes(b"fake-png")
        mirror = Path(tmp) / "mirror"
        cfg = {"mirror_dir": str(mirror)}
        dest = image_gen.mirror_output(str(src), cfg)
        check("mirror 副本创建", dest is not None and Path(dest).exists())
        check("mirror 副本内容一致", Path(dest).read_bytes() == b"fake-png")
        check("mirror 留空不复制", image_gen.mirror_output(str(src), {"mirror_dir": ""}) is None)


def test_pick_best_text_model() -> None:
    models = [
        "gemini-2.5-pro",
        "gemini-3.5-flash",
        "gemini-3.6-flash",
        "gemini-3.1-pro-preview",
        "gemini-3-pro-image",
        "veo-3.0-generate-001",
        "fake-gemini-3.6-flash",
    ]
    best = image_gen.pick_best_text_model(models)
    check("最佳文本模型选中 3.6-flash", best == "gemini-3.6-flash")
    check("无文本模型返回空", image_gen.pick_best_text_model(["gemini-3-pro-image", "veo-3.0"]) == "")


def test_translator_system_prompt() -> None:
    zh = image_gen.build_translator_system("zh")
    en = image_gen.build_translator_system("en")
    check("中文系统提示词包含结构要求", "主体" in zh and "画面文字" in zh)
    check("英文系统提示词包含结构要求", "subject" in en.lower() and "composition" in en.lower())


def test_translate_off_passthrough() -> None:
    cfg = json.loads(json.dumps(image_gen.DEFAULT_CONFIG))
    cfg["translator"]["engine"] = "off"
    r = image_gen.translate_prompt("测试需求", cfg=cfg)
    check("直传模式原文返回", r["engine_used"] == "off" and r["rewritten"] == "测试需求")


def test_looks_broken() -> None:
    check("问号回复判为异常", image_gen._looks_broken("你似乎只发了问号", had_cjk=True))
    check("正常中文回复不判异常", not image_gen._looks_broken("少女站在雪地中", had_cjk=True))
    check("纯英文回复不判异常", not image_gen._looks_broken("A girl in snow", had_cjk=False))


def test_parse_tiered_issues() -> None:
    sample = (
        "人物问题：无\n"
        "背景问题：\n"
        "1. 地面缺少圆形阴影\n"
        "2. 背景音符太少"
    )
    parts = image_gen._parse_tiered_issues(sample)
    check("tiered 解析出人物问题", parts["character"] == "")
    check("tiered 解析出背景问题", "地面缺少圆形阴影" in parts["background"])
    check("tiered 解析出第二条", "背景音符太少" in parts["background"])
    check("tiered 标记已解析", parts.get("parsed") is True)

    sample2 = "人物问题：\n1. 耳机只画了一只耳罩\n背景问题：无"
    parts2 = image_gen._parse_tiered_issues(sample2)
    check("tiered 解析人物问题", "耳机只画了一只耳罩" in parts2["character"])
    check("tiered 人物背景都无", parts2["background"] == "")

    sample3 = "这个画面整体不错"
    parts3 = image_gen._parse_tiered_issues(sample3)
    check("tiered 无标题时整段按人物级", parts3["character"] == sample3)
    check("tiered 无标题标记未解析", parts3.get("parsed") is False)


def test_count_issues() -> None:
    check("计数 0", image_gen._count_issues("无") == 0)
    check("计数空串", image_gen._count_issues("") == 0)
    check("计数单条", image_gen._count_issues("1. 耳机少一只") == 1)
    check("计数多条", image_gen._count_issues("1. 耳机少一只\n2. 缺阴影\n3. 背景太乱") == 3)


def test_build_fix_instruction() -> None:
    instruction = image_gen.build_fix_instruction("1. 耳机只画了一只耳罩\n2. 地面缺少圆形阴影")
    check("修正指令要求保持原样", "保持原样" in instruction)
    check("修正指令包含问题", "耳机只画了一只耳罩" in instruction)
    check("修正指令包含问题2", "地面缺少圆形阴影" in instruction)
    check("修正指令禁止文字", "不要出现任何文字" in instruction)
    empty = image_gen.build_fix_instruction("无")
    check("修正指令空问题处理", "无需修改" in empty)


def test_fix_accepted() -> None:
    old = {
        "ok": True,
        "has_issues": True,
        "has_character_issues": False,
        "issue_counts": {"character": 0, "background": 1},
    }
    good = {
        "ok": True,
        "has_issues": False,
        "has_character_issues": False,
        "issue_counts": {"character": 0, "background": 0},
    }
    bad = {
        "ok": True,
        "has_issues": True,
        "has_character_issues": True,
        "issue_counts": {"character": 1, "background": 0},
    }
    unchanged = {
        "ok": True,
        "has_issues": True,
        "has_character_issues": False,
        "issue_counts": {"character": 0, "background": 1},
    }
    more_bg = {
        "ok": True,
        "has_issues": True,
        "has_character_issues": False,
        "issue_counts": {"character": 0, "background": 2},
    }
    improved_char = {
        "ok": True,
        "has_issues": True,
        "has_character_issues": True,
        "issue_counts": {"character": 1, "background": 0},
    }
    accepted, _ = image_gen._fix_accepted(old, good)
    check("保留最佳：修好则保留", accepted)
    accepted, why = image_gen._fix_accepted(old, bad)
    check("保留最佳：引入人物错误则退回", not accepted)
    check("保留最佳：退回原因含人物", "人物" in why)
    accepted, _ = image_gen._fix_accepted(old, unchanged)
    check("保留最佳：没有变差则保留", accepted)
    accepted, _ = image_gen._fix_accepted(old, more_bg)
    check("保留最佳：背景问题变多则退回", not accepted)
    accepted, _ = image_gen._fix_accepted(
        {"ok": True, "has_issues": True, "has_character_issues": True,
         "issue_counts": {"character": 3, "background": 2}},
        improved_char,
    )
    check("保留最佳：人物级错误减少则保留", accepted)


def test_auto_fix_edit_loop() -> None:
    """全流程离线测试：编辑模式 + 保留最佳（改坏退回 / 修好保留 / 背景细节不重画）。"""
    base_check = {
        "ok": True,
        "has_issues": True,
        "has_character_issues": False,
        "issues": "人物问题：\n无\n\n背景问题：\n1. 缺阴影",
        "character_issues": "",
        "background_issues": "1. 缺阴影",
        "issue_counts": {"character": 0, "background": 1},
    }
    good_check = {
        "ok": True,
        "has_issues": False,
        "has_character_issues": False,
        "issues": "人物问题：\n无\n\n背景问题：\n无",
        "character_issues": "",
        "background_issues": "",
        "issue_counts": {"character": 0, "background": 0},
    }
    bad_check = {
        "ok": True,
        "has_issues": True,
        "has_character_issues": True,
        "issues": "人物问题：\n1. 耳机少一只\n背景问题：\n无",
        "character_issues": "1. 耳机少一只",
        "background_issues": "",
        "issue_counts": {"character": 1, "background": 0},
    }

    with tempfile.TemporaryDirectory() as tmp:
        orig_png = Path(tmp) / "orig.png"
        orig_png.write_bytes(b"\x89PNG-original")
        calls: dict = {"vision": [], "gen": []}

        def fake_check(path, user_text, cfg, tiered=False):
            calls["vision"].append(str(path))
            if str(path).endswith("out-fix1.png"):
                return dict(bad_check)
            if len(calls["vision"]) > 1:
                return dict(good_check)
            return dict(base_check)

        def fake_gen(prompt, **kwargs):
            calls["gen"].append(prompt)
            out = Path(kwargs["out"])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"\x89PNG-new")
            return {"ok": True, "path": str(out), "backend": "vertex", "seed": 1}

        orig_check = image_gen.run_vision_check
        orig_gen = image_gen.generate_image
        image_gen.run_vision_check = fake_check
        image_gen.generate_image = fake_gen
        try:
            # 场景1：编辑模式，修正版更差 -> 自动退回
            result = image_gen.generate_with_translator(
                "测试需求",
                backend="vertex",
                out=str(Path(tmp) / "out.png"),
                init_image=str(orig_png),
                translator="off",
                auto_fix=True,
                fix_mode="edit",
                keep_best=True,
            )
        finally:
            image_gen.run_vision_check = orig_check
            image_gen.generate_image = orig_gen
        af = result.get("auto_fix") or {}
        check(
            "编辑模式修正指令为局部小修",
            any("保持原样" in p for p in calls["gen"]),
        )
        check("改坏时自动退回", af.get("reverted") is True)
        check("退回后结果回到上一版", result["path"].endswith("out.png"))
        check("退回轮次记录完整", len(af.get("history") or []) == 2)
        check("退回原因含人物", "人物" in (af["history"][-1].get("reason") or ""))

        with tempfile.TemporaryDirectory() as tmp2:
            orig2 = Path(tmp2) / "orig.png"
            orig2.write_bytes(b"\x89PNG-original")
            calls2: dict = {"vision": [], "gen": []}

            def fake_check2(path, user_text, cfg, tiered=False):
                calls2["vision"].append(str(path))
                if str(path).endswith("out-fix1.png"):
                    return dict(good_check)
                return dict(base_check)

            def fake_gen2(prompt, **kwargs):
                calls2["gen"].append(prompt)
                out = Path(kwargs["out"])
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(b"\x89PNG-new")
                return {"ok": True, "path": str(out), "backend": "vertex", "seed": 1}

            image_gen.run_vision_check = fake_check2
            image_gen.generate_image = fake_gen2
            try:
                # 场景2：编辑模式，修正版修好 -> 保留
                result2 = image_gen.generate_with_translator(
                    "测试需求",
                    backend="vertex",
                    out=str(Path(tmp2) / "out.png"),
                    init_image=str(orig2),
                    translator="off",
                    auto_fix=True,
                    fix_mode="edit",
                    keep_best=True,
                )
                # 场景3：重画模式，仅背景问题 -> 不重画
                result3 = image_gen.generate_with_translator(
                    "测试需求",
                    backend="vertex",
                    out=str(Path(tmp2) / "out3.png"),
                    init_image=str(orig2),
                    translator="off",
                    auto_fix=True,
                    fix_mode="redraw",
                    keep_best=True,
                )
            finally:
                image_gen.run_vision_check = orig_check
                image_gen.generate_image = orig_gen
            af2 = result2.get("auto_fix") or {}
            check("修好时保留修正版", af2.get("reverted") is False)
            check("修好后结果为新图", result2["path"].endswith("out-fix1.png"))
            check("修好轮次记录完整", len(af2.get("history") or []) == 2)
            af3 = result3.get("auto_fix") or {}
            check("重画模式背景细节不重画", af3.get("rounds") == 0)
            check("重画模式保留原图", result3["path"].endswith("out3.png"))
            check("重画模式给出提示", any("背景" in str(w) for w in result3.get("warnings") or []))


def main() -> int:
    print("=== deepseek-imagegen 冒烟测试 ===")
    test_parse_size()
    test_slugify()
    test_config_merge()
    test_pollinations_url()
    test_comfyui_workflow()
    test_comfyui_img2img_workflow()
    test_probe_image_size()
    test_load_init_image_local()
    test_multipart()
    test_vertex_img2img()
    test_sd_webui_img2img()
    test_comfyui_upload_image()
    test_img2img_routing()
    test_resolve_backend()
    test_default_output_path()
    test_mask_key()
    test_read_first_api_key()
    test_pick_best_image_model()
    test_discover_vertex()
    test_ext_from_content_type()
    test_mirror_output()
    test_pick_best_text_model()
    test_translator_system_prompt()
    test_translate_off_passthrough()
    test_looks_broken()
    test_parse_tiered_issues()
    test_count_issues()
    test_build_fix_instruction()
    test_fix_accepted()
    test_auto_fix_edit_loop()
    print()
    if FAILURES:
        print(f"失败 {len(FAILURES)} 项：{', '.join(FAILURES)}")
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
