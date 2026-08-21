"""Vertex Backend：本地 Vertex Proxy 发现、最佳图像模型挑选、文生图/图生图/画布优先。"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Optional

from ..config import APP_NAME, VERTEX_DEFAULT_DIR
from ..errors import EmptyImageError, GenError
from ..http import http
from ..image_utils import (
    aspect_ratio_key,
    build_canvas_png,
    canvas_size_for,
    multipart,
)
from .base import BACKEND_API_VERSION, BackendCapabilities, ImageBackend
from .openai_images import _extract_image_from_response, gen_openai_image


def read_first_api_key(text: str) -> str:
    """从 api_keys.txt 读取第一个有效 Key（name:sk-... 或单独 sk-...）。"""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("sk-"):
            return line
        parts = line.split(":", 1)
        if len(parts) == 2 and parts[1].strip().startswith("sk-"):
            return parts[1].strip()
    return ""


def pick_best_image_model(models: list[str]) -> str:
    """挑最好的图像模型：非预览、pro > flash > lite、版本更高。"""
    candidates = [m for m in models if "image" in str(m).lower()]
    if not candidates:
        return ""

    def score(model: str) -> int:
        low = model.lower()
        total = 0
        if low.endswith("-preview"):
            total -= 20
        if "pro" in low:
            total += 80
        elif "flash" in low:
            total += 30
        if "lite" in low:
            total -= 10
        version = re.search(r"(\d+)(?:\.(\d+))?", low)
        if version:
            total += int(version.group(1)) * 10 + int(version.group(2) or 0)
        return total

    return max(candidates, key=score)


def parse_models_list(data: dict[str, Any]) -> list[str]:
    """解析代理 models.json 的模型列表，兼容 v1（字符串数组）与 v2（对象数组）。

    新格式示例：{"version": 2, "models": [{"id": "gemini-3-pro-image", "enabled": true, ...}]}
    只收录启用的模型；禁用（enabled=false）的跳过。
    """
    raw = data.get("models", []) if isinstance(data, dict) else []
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, str):
            mid = item.strip()
            if mid:
                out.append(mid)
        elif isinstance(item, dict):
            mid = str(item.get("id") or "").strip()
            enabled = item.get("enabled", True)
            if mid and enabled:
                out.append(mid)
    return out


def pick_best_text_model(models: list[str]) -> str:
    """挑最适合当翻译官的聊天模型（排除图像/音频/翻译等专用模型）。"""
    skip = (
        "image", "tts", "audio", "veo", "lyria", "chirp", "translate",
        "virtual-try-on", "live", "omni",
    )
    candidates = []
    for m in models:
        low = str(m).lower()
        if any(s in low for s in skip):
            continue
        if low.startswith(("fake", "假流式", "假")):
            continue
        candidates.append(m)
    if not candidates:
        return ""

    def score(model: str) -> int:
        low = model.lower()
        total = 0
        if low.endswith("-preview"):
            total -= 60
        if "pro" in low:
            total += 20
        elif "flash" in low:
            total += 30
        if "lite" in low:
            total -= 10
        version = re.search(r"(\d+)(?:\.(\d+))?", low)
        if version:
            total += int(version.group(1)) * 10 + int(version.group(2) or 0)
        return total

    return max(candidates, key=score)


def discover_vertex(cfg: dict[str, Any]) -> dict[str, Any]:
    """读取本地 Vertex Proxy 的端口、密钥与模型列表，返回连接信息。"""
    bc = cfg.get("vertex", {}) if isinstance(cfg.get("vertex"), dict) else {}
    vdir = (
        str(bc.get("dir") or os.environ.get("VERTEX_PROXY_DIR") or VERTEX_DEFAULT_DIR)
        .strip()
        .strip('"')
    )
    if not vdir:
        raise GenError("未配置 vertex.dir（Vertex Proxy 目录）。")
    proxy_cfg_file = os.path.join(vdir, "config", "config.json")
    if not os.path.isfile(proxy_cfg_file):
        raise GenError(f"未找到 Vertex Proxy 配置：{proxy_cfg_file}，请检查 vertex.dir。")
    with open(proxy_cfg_file, "r", encoding="utf-8") as handle:
        proxy_cfg = json.load(handle)
    port = int(proxy_cfg.get("port_api", 2156))

    base_url = str(bc.get("base_url") or "").strip().rstrip("/")
    if not base_url:
        base_url = f"http://127.0.0.1:{port}/v1"

    api_key = str(bc.get("api_key") or "").strip()
    if not api_key:
        keys_file = os.path.join(vdir, "config", "api_keys.txt")
        if os.path.isfile(keys_file):
            with open(keys_file, "r", encoding="utf-8") as handle:
                api_key = read_first_api_key(handle.read())

    models: list[str] = []
    models_file = os.path.join(vdir, "config", "models.json")
    if os.path.isfile(models_file):
        with open(models_file, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        models = parse_models_list(data)

    model = str(bc.get("model") or "").strip()
    if not model and models:
        model = pick_best_image_model(models)
    if not model:
        raise GenError("Vertex Proxy 模型列表中未找到图像模型，请检查 config/models.json。")
    if not api_key:
        raise GenError("Vertex Proxy 未找到 API Key，请检查 config/api_keys.txt 或配置 vertex.api_key。")

    return {
        "dir": vdir,
        "port": port,
        "base_url": base_url,
        "api_key": api_key,
        "models": models,
        "image_models": [m for m in models if "image" in str(m).lower()],
        "model": model,
    }


def vertex_gen_size_string(width: int, height: int) -> str:
    """把目标尺寸映射为代理文生图接口能接受的尺寸字符串。

    实测：该代理只接受 "1024x1536"，其余尺寸返回空数据，且输出固定 1408x768；
    因此只在 3:2 横版时走文生图，其余画幅由 Engine 自动升级为画布优先。
    """
    if aspect_ratio_key(width, height) == (3, 2) or (width, height) == (1408, 768):
        return "1024x1536"
    return f"{width}x{height}"


def gen_vertex(
    cfg: dict[str, Any],
    prompt: str,
    width: int,
    height: int,
    model: str,
    empty_retries: int = 2,
    retry_delay_base: float = 6.0,
) -> bytes:
    """文生图（仅 3:2 横版直出，其余画幅走画布优先）。"""
    info = discover_vertex(cfg)
    model = model or info["model"]
    return gen_openai_image(
        info["base_url"],
        info["api_key"],
        model,
        prompt,
        width,
        height,
        size_str=vertex_gen_size_string(width, height),
        empty_retries=empty_retries,
        retry_delay_base=retry_delay_base,
    )


def gen_vertex_img2img(
    cfg: dict[str, Any],
    prompt: str,
    width: int,
    height: int,
    model: str,
    images: list[tuple[bytes, str, str]],
) -> bytes:
    """调用本地代理 /images/edits 做图生图 / 画布优先 / 参考图（支持多图）。"""
    info = discover_vertex(cfg)
    model = model or info["model"]
    size_field = f"{width}x{height}" if width and height else "auto"
    files = [("image", name, data, mime) for (data, mime, name) in images]
    body, content_type = multipart(
        {"model": model, "prompt": prompt, "n": "1", "size": size_field},
        files,
    )
    status, resp_body, resp_ctype = http(
        f"{info['base_url'].rstrip('/')}/images/edits",
        method="POST",
        headers={
            "Authorization": f"Bearer {info['api_key']}",
            "Content-Type": content_type,
            "User-Agent": f"{APP_NAME}/1.0",
        },
        raw_body=body,
    )
    return _extract_image_from_response(resp_body)


def gen_vertex_canvas_first(
    cfg: dict[str, Any],
    prompt: str,
    width: int,
    height: int,
    model: str,
    empty_retries: int = 2,
    retry_delay_base: float = 6.0,
) -> bytes:
    """画布优先兜底：先建目标画幅空白画布，再走图生图让模型在上面作画。"""
    info = discover_vertex(cfg)
    model = model or info["model"]
    canvas_w, canvas_h = canvas_size_for(width, height)
    canvas = build_canvas_png(canvas_w, canvas_h)
    last_err = ""
    for attempt in range(max(1, empty_retries + 1)):
        try:
            return gen_vertex_img2img(
                cfg, prompt, canvas_w, canvas_h, model, [(canvas, "image/png", "canvas.png")]
            )
        except EmptyImageError as exc:
            last_err = str(exc)
            if attempt < empty_retries:
                time.sleep(retry_delay_base * (2**attempt))
                continue
            raise
    raise GenError(last_err or "画布优先生成失败（上游未返回图片）")


class VertexBackend(ImageBackend):
    """本地 Vertex Proxy 后端。"""

    id = "vertex"
    api_version = BACKEND_API_VERSION

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            text_to_image=True,
            image_to_image=True,
            multi_reference=True,
            quality=False,
            seed=False,
            exact_size=False,
        )

    def list_models(self, cfg: dict[str, Any]) -> list[str]:
        return list(discover_vertex(cfg).get("image_models") or [])

    def resolve_model(self, cfg: dict[str, Any], requested: str = "") -> str:
        requested = (requested or "").strip()
        if requested:
            return requested
        return discover_vertex(cfg)["model"]

    def generate(
        self,
        cfg: dict[str, Any],
        prompt: str,
        width: int,
        height: int,
        model: str = "",
        empty_retries: int = 2,
        retry_delay_base: float = 6.0,
    ) -> bytes:
        return gen_vertex(
            cfg, prompt, width, height, model,
            empty_retries=empty_retries,
            retry_delay_base=retry_delay_base,
        )

    def generate_fallback_size(
        self,
        cfg: dict[str, Any],
        prompt: str,
        width: int,
        height: int,
        model: str = "",
        empty_retries: int = 2,
        retry_delay_base: float = 6.0,
    ) -> bytes:
        """尺寸不符时改用画布优先（代理文生图不守尺寸的实测结论）。"""
        return gen_vertex_canvas_first(
            cfg, prompt, width, height, model,
            empty_retries=empty_retries,
            retry_delay_base=retry_delay_base,
        )

    def edit(
        self,
        cfg: dict[str, Any],
        prompt: str,
        width: int,
        height: int,
        model: str,
        images: list[tuple[bytes, str, str]],
        **kwargs: Any,
    ) -> bytes:
        return gen_vertex_img2img(cfg, prompt, width, height, model, images)
