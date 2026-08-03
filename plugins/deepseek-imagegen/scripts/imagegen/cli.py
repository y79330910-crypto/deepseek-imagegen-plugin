"""命令行入口：generate / translate / config / doctor / list-models。"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
from typing import Any, Optional

from . import generate as gen_mod
from .config import CONFIG_FILE, load_config, mask_config
from .doctor import cmd_doctor
from .http import GenError
from .translator import translate_prompt
from .vertex import discover_vertex


def configure_console_utf8() -> None:
    """修复 Windows PowerShell 中文乱码。"""
    if os.name == "nt":
        try:
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleOutputCP(65001)
            kernel32.SetConsoleCP(65001)
        except Exception:  # noqa: BLE001
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


def cmd_generate(args: argparse.Namespace) -> dict[str, Any]:
    images = list(args.image or [])
    if len(images) > 1:
        raise GenError("当前版本仅支持 1 张参考图（多图组合将在后续版本支持）。")
    return gen_mod.generate(
        args.prompt,
        out=args.out,
        size=args.size,
        seed=args.seed,
        model=args.model,
        init_image=images[0] if images else None,
        denoise=args.denoise,
        translator=args.translator,
        composition=args.composition,
        size_policy=args.size_policy,
        library_enabled=getattr(args, "library", None),
        ref_type=getattr(args, "ref_type", "auto"),
    )


def cmd_translate(args: argparse.Namespace) -> dict[str, Any]:
    result = translate_prompt(
        args.prompt,
        engine=getattr(args, "engine", "auto"),
        feedback=getattr(args, "feedback", ""),
    )
    result["ok"] = True
    return result


def cmd_config(args: argparse.Namespace) -> dict[str, Any]:
    cfg = load_config()
    return {
        "config_file": str(CONFIG_FILE),
        "config_exists": CONFIG_FILE.exists(),
        "config": mask_config(cfg),
    }


def cmd_list_models(args: argparse.Namespace) -> dict[str, Any]:
    cfg = load_config()
    result: dict[str, Any] = {"ok": True, "models": {}}
    try:
        info = discover_vertex(cfg)
        result["models"]["vertex"] = {
            "base_url": info["base_url"],
            "best_model": info["model"],
            "image_models": info["image_models"],
        }
    except GenError as exc:
        result["models"]["vertex"] = f"不可用：{exc}"
    return result


def _print_result(result: dict[str, Any], use_json: bool) -> int:
    if use_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok", True) else 1
    if "path" in result:
        print(f"后端：{result['backend']}")
        print(f"输出：{result['path']}")
        actual = result.get("actual_size")
        match = result.get("size_match")
        if actual:
            mark = "✓" if match else "✗"
            print(f"尺寸：请求 {result['size']} → 实际 {actual} {mark}")
        else:
            print(f"尺寸：请求 {result['size']}（无法读取实际尺寸）")
        print(f"种子：{result['seed']}")
        if result.get("composition_preset") and result["composition_preset"] != "auto":
            print(f"构图预设：{result['composition_preset']}")
        refinfo = result.get("reference") or {}
        if refinfo.get("type"):
            print(
                f"参考图类型：{refinfo.get('label') or refinfo.get('type')}"
                f"（识别方式：{refinfo.get('method')}）"
            )
        if result.get("init_image"):
            print(
                f"图生图：原图 {result['init_image']}"
                + (f"  去噪强度：{result.get('denoise')}" if result.get("denoise") else "")
            )
        if result.get("warnings"):
            for warn in result["warnings"]:
                print(f"提示：{warn}")
    elif "checks" in result:
        print(
            f"配置文件：{result['config_file']}"
            f"（{'存在' if result['config_exists'] else '不存在，使用默认配置'}）"
        )
        print(f"后端：{result['backend']}")
        for check in result["checks"]:
            extra = ""
            if check.get("best_model"):
                extra = f"（最佳模型：{check['best_model']}，共 {check.get('model_count')} 个）"
            print(f"  [{'OK' if check['ok'] else 'FAIL'}] {check['backend']}: {check['message']}{extra}")
    elif "probes" in result:
        print(f"尺寸探针（后端：{result['backend']}）")
        for p in result["probes"]:
            print(
                f"  请求 {p['requested']}：文生图直出={p.get('generations')} | "
                f"画布优先={p.get('canvas_first') or '不支持'} → {p.get('verdict')}"
            )
        if result.get("cache_saved"):
            print("结论已缓存到配置：" + str(result.get("cache_path") or ""))
        else:
            print("（缓存写入失败，不影响本次结果）")
    elif "models" in result:
        for name, models in result["models"].items():
            print(f"[{name}]")
            if isinstance(models, dict):
                for key, value in models.items():
                    if key == "image_models":
                        print(f"  图像模型：{'、'.join(value) if value else '（无）'}")
                    else:
                        print(f"  {key}：{value}")
            else:
                print(f"  {models}")
    elif "config" in result:
        print(f"配置文件：{result['config_file']}")
        print(json.dumps(result["config"], ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="image_gen.py",
        description="DeepSeek ImageGen 桥接脚本：本地 Vertex Proxy 生成图片并保存。",
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON（机器可读）")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="生成图片")
    gen.add_argument("prompt", help="提示词")
    gen.add_argument("--out", help="输出文件路径")
    gen.add_argument("--size", default="", help="分辨率，如 1024x1024（图生图省略时自动取原图尺寸）")
    gen.add_argument("--seed", type=int, default=None, help="随机种子")
    gen.add_argument("--model", default="", help="模型（默认自动选最佳图像模型）")
    gen.add_argument(
        "--image",
        action="append",
        default=None,
        help="参考图片（图生图）：本地路径或 http(s) 链接；当前仅支持 1 张",
    )
    gen.add_argument(
        "--ref-type",
        dest="ref_type",
        default="auto",
        choices=["auto", "character", "outfit", "style", "scene", "composition", "pose", "object"],
        help="参考图类型：auto 自动识别（默认）/ character 角色 / outfit 服装 / style 风格 / "
             "scene 场景 / composition 构图 / pose 姿势 / object 物品",
    )
    gen.add_argument("--denoise", type=float, default=None, help="去噪强度 0~1（图生图，默认 0.6）")
    gen.add_argument(
        "--translator",
        default="auto",
        choices=["auto", "deepseek", "gemini", "off"],
        help="提示词翻译官：deepseek(默认) / gemini / off(直传) / auto(跟随配置)",
    )
    gen.add_argument(
        "--composition",
        default="auto",
        choices=["auto", "full-body", "half-body", "portrait", "landscape"],
        help="构图预设：full-body 全身竖版 / half-body 半身 / portrait 特写 / landscape 横版",
    )
    gen.add_argument(
        "--size-policy",
        dest="size_policy",
        default="",
        choices=["", "strict", "auto", "warn"],
        help="尺寸不符策略：strict 严格报错 / auto 自动兜底重试(默认) / warn 仅警告",
    )
    lib_group = gen.add_mutually_exclusive_group()
    lib_group.add_argument(
        "--library", dest="library", action="store_true", default=None,
        help="生成时启用提示词词库检索（默认跟随配置）",
    )
    lib_group.add_argument(
        "--no-library", dest="library", action="store_false",
        help="生成时不使用提示词词库",
    )
    gen.add_argument("--json", action="store_true", help="输出 JSON（机器可读）")

    tr = sub.add_parser("translate", help="把用户需求改写成结构化生图提示词（翻译官）")
    tr.add_argument("prompt", help="用户需求（中文即可）")
    tr.add_argument(
        "--engine",
        default="auto",
        choices=["auto", "deepseek", "gemini", "off"],
        help="翻译官引擎：auto(跟随配置) / deepseek / gemini / off",
    )
    tr.add_argument("--feedback", default="", help="上次生成的问题反馈，用于修正重写")
    tr.add_argument("--json", action="store_true", help="输出 JSON（机器可读）")

    doctor = sub.add_parser("doctor", help="诊断本地 Vertex 代理连通性")
    doctor.add_argument(
        "--size-probe", dest="size_probe", action="store_true",
        help="实测代理是否遵守尺寸参数（生成小图核对，结果缓存进配置）",
    )
    doctor.add_argument("--size", default="", help="探针尺寸（默认 竖版/横版/正方形 三档）")
    doctor.add_argument("--json", action="store_true", help="输出 JSON（机器可读）")

    config_parser = sub.add_parser("config", help="查看当前生效配置（密钥打码）")
    config_parser.add_argument("--json", action="store_true", help="输出 JSON（机器可读）")

    models_parser = sub.add_parser("list-models", help="查看本地代理可用模型")
    models_parser.add_argument("--json", action="store_true", help="输出 JSON（机器可读）")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    configure_console_utf8()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "generate":
            result = cmd_generate(args)
        elif args.command == "translate":
            result = cmd_translate(args)
        elif args.command == "doctor":
            result = cmd_doctor(args)
        elif args.command == "config":
            result = cmd_config(args)
        elif args.command == "list-models":
            result = cmd_list_models(args)
        else:
            parser.error(f"未知命令：{args.command}")
            return 2
        return _print_result(result, use_json=args.json)
    except GenError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        else:
            print(f"错误：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("已取消。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
