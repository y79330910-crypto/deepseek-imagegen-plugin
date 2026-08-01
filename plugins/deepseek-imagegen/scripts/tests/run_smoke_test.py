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
    check("resolve auto -> 默认", image_gen.resolve_backend("auto", cfg) == "pollinations")
    check("resolve 别名", image_gen.resolve_backend("webui", cfg) == "sd-webui")
    check("resolve comfy", image_gen.resolve_backend("comfy", cfg) == "comfyui")


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
    print()
    if FAILURES:
        print(f"失败 {len(FAILURES)} 项：{', '.join(FAILURES)}")
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
