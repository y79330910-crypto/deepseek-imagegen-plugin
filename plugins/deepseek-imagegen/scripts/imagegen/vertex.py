"""本地 Vertex Proxy：自动发现端口/密钥/模型列表，选最佳图像模型，文生图/图生图/画布优先。"""

from __future__ import annotations

import json
import math
import os
import re
import time
from typing import Any, Optional

from .config import APP_NAME, VERTEX_DEFAULT_DIR
from .http import EmptyImageError, GenError, http
from .image_utils import (
    aspect_ratio_key,
    build_canvas_png,
    canvas_size_for,
    multipart,
    probe_image_size_ext,
    sizes_match,
)


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


def _extract_image_from_response(body: bytes) -> bytes:
    """从 OpenAI 兼容图像接口的 JSON 响应中提取第一张图片字节。"""
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GenError(f"图像接口返回了无法解析的内容：{body[:300]!r}") from exc
    if isinstance(data, dict) and data.get("error"):
        err = data["error"]
        raise GenError(f"图像接口返回错误：{err.get('message') or err}")
    items = (data.get("data") or []) if isinstance(data, dict) else []
    if not items:
        raise EmptyImageError(
            "上游暂时没有返回图片（接口返回空列表，通常是限流或代理吞掉了错误）。"
        )
    item = items[0]
    b64 = item.get("b64_json") or item.get("image")
    if b64:
        import base64

        try:
            return base64.b64decode(b64)
        except Exception as exc:  # noqa: BLE001
            raise GenError(f"图片 base64 解码失败：{exc}") from exc
    url = item.get("url")
    if url:
        if url.startswith("data:image/"):
            import base64

            _, b64 = url.split(",", 1)
            return base64.b64decode(b64)
        _, body, _ctype = http(url)
        return body
    raise GenError(f"图像接口返回中缺少图片数据：{body[:500]!r}")


def gen_openai_image(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    width: int,
    height: int,
    *,
    size_str: str = "",
    quality: str = "",
    empty_retries: int = 2,
    retry_delay_base: float = 6.0,
) -> bytes:
    """调用 OpenAI 兼容 /images/generations（带空结果重试）。"""
    base = base_url.rstrip("/")
    payload = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "size": size_str or f"{width}x{height}",
    }
    if quality:
        payload["quality"] = quality
    last_err = ""
    for attempt in range(max(1, empty_retries + 1)):
        status, body, content_type = http(
            f"{base}/images/generations",
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": f"{APP_NAME}/1.0",
            },
            payload=payload,
            retry_delay_base=retry_delay_base,
        )
        try:
            return _extract_image_from_response(body)
        except EmptyImageError as exc:
            last_err = str(exc)
            if attempt < empty_retries:
                time.sleep(retry_delay_base * (2**attempt))
                continue
            raise
    raise GenError(last_err or "图像接口未返回图片")


def vertex_gen_size_string(width: int, height: int) -> str:
    """把目标尺寸映射为代理文生图接口能接受的尺寸字符串。

    实测：该代理只接受 "1024x1536"，其余尺寸返回空数据，且输出固定 1408x768；
    因此只在 3:2 横版时走文生图，其余画幅由 generate 自动升级为画布优先。
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


# ============ 备用后端（extra_backends，如 DragToken） ============
OPENAI_IMAGE_SIZES = ["1254x1254", "1536x1024", "1024x1536"]

# 按模型名关键词预置的尺寸白名单（未在配置里自定义 sizes 时使用）
EXTRA_BACKEND_SIZE_PRESETS = [
    ("原生4k", ["2048x2048", "3840x2160", "2160x3840"]),
    ("4k超分", ["2048x2048", "2560x1440", "3840x2160", "2160x3840", "3696x1584"]),
]
EXTRA_QUALITIES = ("auto", "low", "medium", "high")


def parse_size_list(raw: Any) -> list[str]:
    """把配置里的尺寸白名单解析成列表（支持逗号分隔字符串或数组）。"""
    if isinstance(raw, list):
        items = [str(x) for x in raw]
    else:
        items = [str(x) for x in str(raw or "").split(",")]
    out: list[str] = []
    seen: set[str] = set()
    for s in items:
        s = s.strip().lower()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def extra_size_whitelist(info: dict[str, Any]) -> list[str]:
    """备用后端尺寸白名单：配置 sizes > 按模型关键词预置 > 标准三档。"""
    custom = parse_size_list(info.get("sizes"))
    if custom:
        return custom
    model = str(info.get("model") or "").lower()
    for keyword, sizes in EXTRA_BACKEND_SIZE_PRESETS:
        if keyword in model:
            return sizes
    return list(OPENAI_IMAGE_SIZES)


def _size_ratio(size_str: str) -> float:
    try:
        w, h = (int(x) for x in str(size_str).lower().split("x", 1))
        return w / h if h else 1.0
    except (ValueError, AttributeError):
        return 0.0


def _size_kind(size_str: str) -> str:
    try:
        w, h = (int(x) for x in str(size_str).lower().split("x", 1))
    except (ValueError, AttributeError):
        return "square"
    if h > w:
        return "portrait"
    if w > h:
        return "landscape"
    return "square"


def normalize_extra_size(
    width: int, height: int, whitelist: Optional[list[str]] = None
) -> str:
    """把目标尺寸映射到白名单：方向优先 + 画幅最接近 + 像素量最接近。

    whitelist 缺省时退回 OpenAI 标准三档（保持旧行为）。
    """
    sizes = whitelist if whitelist else OPENAI_IMAGE_SIZES
    if not sizes:
        raise GenError("备用后端尺寸白名单为空，请检查配置 extra_backends.<name>.sizes。")
    wanted = f"{width}x{height}".lower()
    if wanted in sizes:
        return wanted
    target_kind = "portrait" if height > width else ("landscape" if width > height else "square")
    same_kind = [s for s in sizes if _size_kind(s) == target_kind]
    pool = same_kind or sizes
    target_ratio = width / height if height else 1.0
    target_area = width * height
    best, best_key = "", None
    for s in pool:
        sw, sh = 0, 0
        try:
            sw, sh = (int(x) for x in s.split("x", 1))
        except (ValueError, AttributeError):
            pass
        key = (abs(_size_ratio(s) - target_ratio), abs(sw * sh - target_area))
        if best_key is None or key < best_key:
            best, best_key = s, key
    return best


def extra_size_aspect(size_str: str) -> str:
    """把尺寸字符串转成画幅比例标签（用于写进提示词）。"""
    known = {"1254x1254": "1:1", "1536x1024": "3:2", "1024x1536": "2:3"}
    key = str(size_str)
    if key in known:
        return known[key]
    try:
        w, h = (int(x) for x in key.lower().split("x", 1))
    except (ValueError, AttributeError):
        return ""
    if w <= 0 or h <= 0:
        return ""
    g = math.gcd(w, h)
    return f"{w // g}:{h // g}"


def extra_backend_sizes(cfg: dict[str, Any], name: str, model_override: str = "") -> list[str]:
    """备用后端实际尺寸白名单：--model 覆盖优先于配置里的 model。"""
    backends = cfg.get("extra_backends") or {}
    info = backends.get(name) if isinstance(backends, dict) else None
    if not isinstance(info, dict):
        raise GenError(f"未找到备用后端「{name}」，请检查配置 extra_backends。")
    eff_model = model_override or str(info.get("model") or "").strip()
    return extra_size_whitelist({"model": eff_model, "sizes": info.get("sizes")})


def pick_extra_model_for_size(cfg: dict[str, Any], name: str, width: int, height: int) -> str:
    """备用后端模型为空（自动）时，按目标尺寸挑选最合适的模型。

    优先选白名单里能精确命中目标尺寸的模型；都不行则选画幅/比例最接近的。
    返回空字符串表示没有可用模型，沿用默认。
    """
    backends = cfg.get("extra_backends") or {}
    info = backends.get(name) if isinstance(backends, dict) else None
    if not isinstance(info, dict):
        return ""
    models = [str(x).strip() for x in (info.get("models") or []) if str(x).strip()]
    default = str(info.get("model") or "").strip()
    if default and default not in models:
        models.insert(0, default)
    if not models:
        return ""
    wanted = f"{width}x{height}".lower()
    target_kind = "portrait" if height > width else ("landscape" if width > height else "square")
    target_ratio = width / height if height else 1.0
    best, best_key = "", None
    for m in models:
        wl = extra_size_whitelist({"model": m, "sizes": info.get("sizes")})
        if not wl:
            continue
        if wanted in wl:
            return m
        slot = normalize_extra_size(width, height, wl)
        key = (
            1 if _size_kind(slot) != target_kind else 0,
            abs(_size_ratio(slot) - target_ratio),
        )
        if best_key is None or key < best_key:
            best, best_key = m, key
    return best or default


def discover_extra_backend(cfg: dict[str, Any], name: str) -> dict[str, Any]:
    """读取备用后端 extra_backends.<name>，返回连接信息。"""
    backends = cfg.get("extra_backends") or {}
    if not isinstance(backends, dict):
        backends = {}
    info = backends.get(name)
    if not isinstance(info, dict):
        raise GenError(f"未找到备用后端「{name}」，请检查配置 extra_backends。")
    base_url = str(info.get("base_url") or "").strip().rstrip("/")
    api_key = str(info.get("api_key") or "").strip()
    model = str(info.get("model") or "").strip()
    if not base_url:
        raise GenError(f"备用后端「{name}」缺少 base_url。")
    if not api_key:
        raise GenError(f"备用后端「{name}」缺少 api_key。")
    if not model:
        raise GenError(f"备用后端「{name}」缺少 model。")
    sizes = extra_size_whitelist(info)
    quality = str(info.get("quality") or "").strip().lower()
    if quality and quality not in EXTRA_QUALITIES:
        raise GenError(
            f"备用后端「{name}」的 quality 只支持 {'/'.join(EXTRA_QUALITIES)}（不支持 ultra）。"
        )
    return {
        "name": name,
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
        "sizes": sizes,
        "quality": quality,
    }


def gen_extra_image(
    cfg: dict[str, Any],
    name: str,
    prompt: str,
    width: int,
    height: int,
    model: str = "",
    size_str: str = "",
    quality: str = "",
    empty_retries: int = 2,
    retry_delay_base: float = 6.0,
) -> bytes:
    """备用后端文生图（OpenAI 兼容 /images/generations）。"""
    info = discover_extra_backend(cfg, name)
    model = model or info["model"]
    size = size_str or normalize_extra_size(width, height, extra_backend_sizes(cfg, name, model))
    q = quality or info.get("quality") or ""
    return gen_openai_image(
        info["base_url"],
        info["api_key"],
        model,
        prompt,
        width,
        height,
        size_str=size,
        quality=q,
        empty_retries=empty_retries,
        retry_delay_base=retry_delay_base,
    )


def gen_extra_img2img(
    cfg: dict[str, Any],
    name: str,
    prompt: str,
    width: int,
    height: int,
    model: str,
    images: list[tuple[bytes, str, str]],
    size_str: str = "",
    quality: str = "",
) -> bytes:
    """备用后端图生图（OpenAI 兼容 /images/edits，支持多图）。"""
    info = discover_extra_backend(cfg, name)
    model = model or info["model"]
    size_field = size_str or normalize_extra_size(width, height, extra_backend_sizes(cfg, name, model))
    fields: dict[str, Any] = {"model": model, "prompt": prompt, "n": "1", "size": size_field}
    q = quality or info.get("quality") or ""
    if q:
        fields["quality"] = q
    files = [("image", name, data, mime) for (data, mime, name) in images]
    body, content_type = multipart(
        fields,
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
