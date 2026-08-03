"""诊断：本地代理连通性检查 + 尺寸探针（结果缓存进配置）。"""

from __future__ import annotations

import argparse
import json
import time
from typing import Any, Optional

from .config import CONFIG_FILE, load_config, save_config
from .http import BROWSER_UA, HEALTH_TIMEOUT, GenError, http
from .image_utils import parse_size, probe_image_size_ext, sizes_match
from .vertex import discover_vertex, gen_vertex, gen_vertex_canvas_first


def _health_check(label: str, check: Any, cfg: dict[str, Any]) -> dict[str, Any]:
    entry: dict[str, Any] = {"backend": label, "ok": True, "message": "正常"}
    try:
        check(cfg)
    except GenError as exc:
        entry["ok"] = False
        entry["message"] = str(exc)
    except Exception as exc:  # noqa: BLE001
        entry["ok"] = False
        entry["message"] = f"{type(exc).__name__}: {exc}"
    return entry


def save_probe_cache(backend: str, probes: list[dict[str, Any]]) -> str:
    """把尺寸探针结果缓存进用户配置。"""
    cfg = {}
    if CONFIG_FILE.exists():
        try:
            with CONFIG_FILE.open("r", encoding="utf-8") as handle:
                cfg = json.load(handle)
        except (OSError, json.JSONDecodeError):
            cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    sp = cfg.get("size_policy") or {}
    if not isinstance(sp, dict):
        sp = {}
    cache = sp.get("probe_cache") or {}
    if not isinstance(cache, dict):
        cache = {}
    cache[backend] = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "probes": probes,
    }
    sp["probe_cache"] = cache
    cfg["size_policy"] = sp
    return save_config(cfg)


def run_size_probe(cfg: dict[str, Any], size: str = "") -> dict[str, Any]:
    """尺寸探针：实测代理是否遵守尺寸参数（doctor --size-probe）。"""
    backend = "vertex"
    targets: list[tuple[int, int]] = []
    if size.strip():
        targets.append(parse_size(size))
    else:
        targets = [(768, 1408), (1408, 768), (1024, 1024)]
    prompt = "纯色渐变测试图，浅灰到白色，画面中央一个深灰色圆点，无文字无水印"
    probes: list[dict[str, Any]] = []
    for w, h in targets:
        item: dict[str, Any] = {
            "requested": f"{w}x{h}",
            "generations": None,
            "canvas_first": None,
            "verdict": "",
        }
        try:
            data = gen_vertex(cfg, prompt, w, h, "", empty_retries=1)
            actual = probe_image_size_ext(data, "")
            item["generations"] = f"{actual[0]}x{actual[1]}" if actual else "无法读取"
            item["generations_match"] = bool(actual and sizes_match((w, h), actual).get("ok"))
        except GenError as exc:
            item["generations"] = "失败：" + str(exc)[:120]
            item["generations_match"] = False
        try:
            data = gen_vertex_canvas_first(cfg, prompt, w, h, "", empty_retries=1)
            actual = probe_image_size_ext(data, "")
            item["canvas_first"] = f"{actual[0]}x{actual[1]}" if actual else "无法读取"
            item["canvas_first_match"] = bool(actual and sizes_match((w, h), actual).get("ok"))
        except GenError as exc:
            item["canvas_first"] = "失败：" + str(exc)[:120]
            item["canvas_first_match"] = False
        if item.get("generations_match"):
            item["verdict"] = "文生图直出即可"
        elif item.get("canvas_first_match"):
            item["verdict"] = "需画布优先"
        else:
            item["verdict"] = "两种方式都无法保证尺寸，建议检查代理"
        probes.append(item)
    cached = ""
    try:
        cached = save_probe_cache(backend, probes)
    except OSError as exc:
        cached = ""
    return {
        "ok": True,
        "backend": backend,
        "probes": probes,
        "cache_saved": bool(cached),
        "cache_path": cached,
        "message": (
            "尺寸探针完成：结果已缓存到配置，生成时会按实测结论自动选择尺寸写法/画布优先。"
            if cached
            else "尺寸探针完成，但缓存写入失败（不影响本次结果）。"
        ),
    }


def cmd_doctor(args: argparse.Namespace) -> dict[str, Any]:
    cfg = load_config()
    if getattr(args, "size_probe", False):
        return run_size_probe(cfg, size=getattr(args, "size", ""))
    checks: list[dict[str, Any]] = []

    def check_vertex(c: dict[str, Any]) -> None:
        info = discover_vertex(c)
        status, body, content_type = http(
            f"{info['base_url'].rstrip('/')}/models",
            headers={
                "Authorization": f"Bearer {info['api_key']}",
                "User-Agent": BROWSER_UA,
            },
            timeout=HEALTH_TIMEOUT,
        )
        data = json.loads(body.decode("utf-8"))
        ids = [m.get("id") for m in data.get("data", [])]
        if not ids:
            raise GenError("接口可访问但未返回模型列表")
        holder["info"] = info
        holder["count"] = len(ids)

    holder: dict[str, Any] = {}
    vertex_check = _health_check("vertex", check_vertex, cfg)
    if holder.get("info"):
        vertex_check["best_model"] = holder["info"]["model"]
        vertex_check["model_count"] = holder["count"]
    checks.append(vertex_check)
    return {
        "ok": any(check["ok"] for check in checks),
        "config_file": str(CONFIG_FILE),
        "config_exists": CONFIG_FILE.exists(),
        "backend": "vertex",
        "checks": checks,
    }
