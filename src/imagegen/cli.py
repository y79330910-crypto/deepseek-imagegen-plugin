"""命令行入口：generate / translate / config / doctor / list-models。"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import webbrowser
from typing import Any, Optional

from .errors import GenError
from .models import GenerateRequest
from .services import (
    ConfigService,
    DiagnosticService,
    GenerationService,
    ModelService,
)
from .translator import translate_prompt


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
    request = GenerateRequest(
        prompt=args.prompt,
        size=args.size,
        model=args.model,
        seed=args.seed,
        quality=getattr(args, "quality", ""),
        composition=args.composition,
        translator=args.translator,
        images=images,
        reference_roles=list(args.ref_role or []),
        ref_type=getattr(args, "ref_type", "auto"),
        library_enabled=getattr(args, "library", None),
        out=args.out,
        denoise=args.denoise,
    )
    return GenerationService().generate(request).to_dict()


def cmd_translate(args: argparse.Namespace) -> dict[str, Any]:
    result = translate_prompt(
        args.prompt,
        engine=getattr(args, "engine", "auto"),
        feedback=getattr(args, "feedback", ""),
    )
    result["ok"] = True
    return result


def cmd_config(args: argparse.Namespace) -> dict[str, Any]:
    svc = ConfigService()
    return {
        "config_file": str(svc.path()),
        "config_exists": svc.exists(),
        "config": svc.masked(),
    }


def cmd_list_models(args: argparse.Namespace) -> dict[str, Any]:
    svc = ModelService()
    result: dict[str, Any] = {"ok": True, "models": {}}
    for target in ("translator", "image"):
        try:
            result["models"][target] = svc.list_models(target)
        except GenError as exc:
            result["models"][target] = f"不可用：{exc}"
    return result


def cmd_serve(args: argparse.Namespace) -> int:
    from .api import create_server, validate_bind_address

    host = validate_bind_address(args.host, args.allow_remote)
    try:
        server = create_server(host, args.port, config_path=args.config or None)
    except OSError as exc:
        print(f"错误：无法监听 {host}:{args.port}：{exc}", file=sys.stderr)
        return 1
    display_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    url = f"http://{display_host}:{args.port}/"
    print(f"ImageGen WebUI + HTTP API v1 已启动：{url}")
    if getattr(args, "open", False):
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def cmd_doctor(args: argparse.Namespace) -> dict[str, Any]:
    return DiagnosticService().doctor()


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
        if result.get("image_model_used"):
            print(f"图像模型：{result['image_model_used']}")
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
    elif "models" in result:
        for name, models in result["models"].items():
            print(f"[{name}]")
            if isinstance(models, list):
                print(f"  模型：{'、'.join(models) if models else '（无，请检查配置/连通性）'}")
            else:
                print(f"  {models}")
    elif "config" in result:
        print(f"配置文件：{result['config_file']}")
        print(json.dumps(result["config"], ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="imagegen",
        description="ImageGen standalone local image generation application（CLI / HTTP API / WebUI）。",
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON（机器可读）")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="生成图片")
    gen.add_argument("prompt", help="提示词")
    gen.add_argument("--out", help="输出文件路径")
    gen.add_argument("--size", default="", help="分辨率，如 1024x1024（图生图省略时自动取原图尺寸）")
    gen.add_argument("--seed", type=int, default=None, help="随机种子")
    gen.add_argument("--model", default="", help="图像模型（默认使用配置 image.model）")
    gen.add_argument(
        "--image",
        action="append",
        default=None,
        help="参考图片（图生图）：本地路径或 http(s) 链接；可重复指定，最多 4 张",
    )
    gen.add_argument(
        "--ref-type",
        dest="ref_type",
        default="auto",
        choices=["auto", "character", "outfit", "style", "scene", "composition", "pose", "object"],
        help="参考图类型（单图）：auto 自动识别（默认）/ character 角色 / outfit 服装 / style 风格 / "
             "scene 场景 / composition 构图 / pose 姿势 / object 物品",
    )
    gen.add_argument(
        "--ref-role",
        dest="ref_role",
        action="append",
        default=None,
        choices=["auto", "character", "outfit", "style", "scene", "composition", "pose", "object"],
        help="参考图用途（可重复，按 --image 顺序对应，最多 4 张）：character/outfit/style/pose/"
             "scene/composition/object；未指定时第 1 张=角色，其余按 服装→姿势→风格→场景→物品",
    )
    gen.add_argument(
        "--denoise",
        type=float,
        default=None,
        help="已弃用：当前后端不使用去噪强度，参数将被忽略（保留以兼容旧调用）",
    )
    gen.add_argument(
        "--translator",
        default="auto",
        choices=["auto", "deepseek", "gemini", "off"],
        help="提示词处理：auto 跟随配置 / off 直传；deepseek、gemini 为旧兼容值，等价开启",
    )
    gen.add_argument(
        "--composition",
        default="auto",
        choices=["auto", "full-body", "half-body", "portrait", "landscape"],
        help="构图预设：full-body 全身竖版 / half-body 半身 / portrait 特写 / landscape 横版",
    )
    gen.add_argument(
        "--quality",
        default="",
        choices=["", "low", "medium", "high"],
        help="渲染质量：low/medium/high；默认不传（向上游省略 quality 字段）",
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

    doctor = sub.add_parser("doctor", help="诊断提示词 / 图像上游连通性")
    doctor.add_argument("--json", action="store_true", help="输出 JSON（机器可读）")

    config_parser = sub.add_parser("config", help="查看当前生效配置（密钥打码）")
    config_parser.add_argument("--json", action="store_true", help="输出 JSON（机器可读）")

    models_parser = sub.add_parser("list-models", help="查看提示词 / 图像上游可用模型")
    models_parser.add_argument("--json", action="store_true", help="输出 JSON（机器可读）")

    serve = sub.add_parser("serve", help="启动本地 HTTP API v1")
    serve.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    serve.add_argument("--port", type=int, default=8765, help="端口（默认 8765）")
    serve.add_argument(
        "--config",
        default="",
        help="配置文件路径（默认 ~/.deepseek-imagegen/config.json）",
    )
    serve.add_argument(
        "--allow-remote",
        action="store_true",
        help="--allow-remote exposes the ImageGen API to the network.",
    )
    serve.add_argument(
        "--open",
        action="store_true",
        help="Server 启动成功后自动打开浏览器（http://127.0.0.1:<port>/）",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    configure_console_utf8()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            return cmd_serve(args)
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
