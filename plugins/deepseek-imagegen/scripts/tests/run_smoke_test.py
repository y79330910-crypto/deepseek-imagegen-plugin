#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线冒烟测试：不依赖网络，验证 image_gen.py 的核心逻辑。"""

from __future__ import annotations

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


def main() -> int:
    print("=== deepseek-imagegen 冒烟测试 ===")
    test_parse_size()
    test_slugify()
    test_config_merge()
    test_pollinations_url()
    test_comfyui_workflow()
    test_resolve_backend()
    test_default_output_path()
    test_mask_key()
    test_read_first_api_key()
    test_pick_best_image_model()
    test_discover_vertex()
    test_ext_from_content_type()
    print()
    if FAILURES:
        print(f"失败 {len(FAILURES)} 项：{', '.join(FAILURES)}")
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
