"""OpenAI 兼容图像后端：/images/generations 与 /images/edits。

同时承接配置里 extra_backends 的备用后端（如 DragToken gpt-image-2）。
"""

from __future__ import annotations

import base64
import json
import math
import time
from typing import Any, Optional

from ..config import APP_NAME
from ..errors import EmptyImageError, GenError
from ..http import http
from ..image_utils import multipart
from .base import BACKEND_API_VERSION, BackendCapabilities, ImageBackend


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
        try:
            return base64.b64decode(b64)
        except Exception as exc:  # noqa: BLE001
            raise GenError(f"图片 base64 解码失败：{exc}") from exc
    url = item.get("url")
    if url:
        if url.startswith("data:image/"):
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


class OpenAIImagesBackend(ImageBackend):
    """OpenAI 兼容图像后端；name 指定 extra_backends 里的具体后端名。"""

    id = "openai-compatible"
    api_version = BACKEND_API_VERSION

    def __init__(self, name: str = "") -> None:
        self.name = (name or "").strip()

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            text_to_image=True,
            image_to_image=True,
            multi_reference=True,
            quality=True,
            seed=False,
            exact_size=True,
        )

    def list_models(self, cfg: dict[str, Any]) -> list[str]:
        backends = cfg.get("extra_backends") or {}
        info = backends.get(self.name) if isinstance(backends, dict) else None
        if not isinstance(info, dict):
            return []
        models = [str(x).strip() for x in (info.get("models") or []) if str(x).strip()]
        if models:
            return models
        default = str(info.get("model") or "").strip()
        return [default] if default else []

    def resolve_model(self, cfg: dict[str, Any], requested: str = "") -> str:
        requested = (requested or "").strip()
        if requested:
            return requested
        return discover_extra_backend(cfg, self.name)["model"]

    def generate(
        self,
        cfg: dict[str, Any],
        prompt: str,
        width: int,
        height: int,
        model: str = "",
        size_str: str = "",
        quality: str = "",
        empty_retries: int = 2,
        retry_delay_base: float = 6.0,
    ) -> bytes:
        return gen_extra_image(
            cfg, self.name, prompt, width, height, model,
            size_str=size_str, quality=quality,
            empty_retries=empty_retries, retry_delay_base=retry_delay_base,
        )

    def edit(
        self,
        cfg: dict[str, Any],
        prompt: str,
        width: int,
        height: int,
        model: str,
        images: list[tuple[bytes, str, str]],
        size_str: str = "",
        quality: str = "",
        **kwargs: Any,
    ) -> bytes:
        return gen_extra_img2img(
            cfg, self.name, prompt, width, height, model, images,
            size_str=size_str, quality=quality,
        )
