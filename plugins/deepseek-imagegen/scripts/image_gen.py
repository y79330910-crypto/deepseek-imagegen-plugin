#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepSeek ImageGen bridge.

给 Codex 中的 DeepSeek 等纯文本模型提供图像生成能力：
把提示词发给可配置的图像后端，保存生成的 PNG 并返回路径。

后端：
  pollinations   免费公共 API，无需 Key
  siliconflow    OpenAI 兼容图像接口（FLUX 等），需要 api_key
  vertex         本地 Vertex Proxy（OpenAI 兼容，自动读取端口/密钥/模型列表）
  sd-webui       本地 Stable Diffusion WebUI / Forge（http://127.0.0.1:7860）
  comfyui        本地 ComfyUI（http://127.0.0.1:8188）

图生图（--image 参考图 + --denoise 去噪强度）：
  vertex         本地 Vertex Proxy 的 /images/edits 编辑接口
  sd-webui       本地 SD WebUI 的 /sdapi/v1/img2img
  comfyui        上传图片后 VaeEncode + KSampler（denoise）

配置：~/.deepseek-imagegen/config.json（参考 scripts/config.example.json）
仅使用 Python 标准库，无第三方依赖。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional


APP_NAME = "deepseek-imagegen"
CONFIG_DIR = Path.home() / ".deepseek-imagegen"
CONFIG_FILE = CONFIG_DIR / "config.json"
VERTEX_DEFAULT_DIR = r"C:\Users\yjq\Documents\Codex\2026-07-31\new-chat\outputs\vertex-proxy\dist"
DEFAULT_MIRROR_DIR = r"C:\Users\yjq\Pictures\codex"

DEFAULT_TIMEOUT = 180
HEALTH_TIMEOUT = 8
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

DEFAULT_CONFIG: dict[str, Any] = {
    "default_backend": "vertex",
    "save_dir": "",
    "mirror_dir": DEFAULT_MIRROR_DIR,
    "translator": {
        "enabled": True,
        "engine": "deepseek",
        "output_lang": "zh",
        "auto_fix": True,
        "max_fix_rounds": 1,
        "fix_mode": "edit",
        "fix_keep_best": True,
        "deepseek": {
            "base_url": "",
            "api_key": "",
            "model": "deepseek-v4-flash",
        },
        "gemini": {
            "model": "",
        },
        "vision_bridge": "",
    },
    "pollinations": {
        "base_url": "https://image.pollinations.ai/prompt",
        "model": "",
    },
    "siliconflow": {
        "base_url": "https://api.siliconflow.cn/v1/images/generations",
        "api_key": "",
        "model": "black-forest-labs/FLUX.1-schnell",
        "size": "1024x1024",
    },
    "vertex": {
        "dir": VERTEX_DEFAULT_DIR,
        "base_url": "",
        "api_key": "",
        "model": "",
    },
    "sd_webui": {
        "base_url": "http://127.0.0.1:7860",
        "sampler_name": "Euler a",
        "steps": 28,
        "cfg_scale": 7,
        "denoising_strength": 0.6,
    },
    "comfyui": {
        "base_url": "http://127.0.0.1:8188",
        "checkpoint": "",
        "sampler_name": "euler",
        "scheduler": "normal",
        "steps": 28,
        "cfg": 7,
        "denoise": 0.6,
    },
}

BACKEND_ALIASES = {
    "auto": None,
    "pollinations": "pollinations",
    "pollination": "pollinations",
    "free": "pollinations",
    "siliconflow": "siliconflow",
    "silicon": "siliconflow",
    "flux": "siliconflow",
    "vertex": "vertex",
    "vertex-proxy": "vertex",
    "vproxy": "vertex",
    "proxy": "vertex",
    "local": "vertex",
    "sd": "sd-webui",
    "sd-webui": "sd-webui",
    "a1111": "sd-webui",
    "webui": "sd-webui",
    "comfyui": "comfyui",
    "comfy": "comfyui",
}


class GenError(Exception):
    """可向用户展示的错误。"""


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config() -> dict[str, Any]:
    """读取用户配置并合并默认值。"""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if CONFIG_FILE.exists():
        try:
            with CONFIG_FILE.open("r", encoding="utf-8") as handle:
                user_cfg = json.load(handle)
            if isinstance(user_cfg, dict):
                cfg = _deep_merge(cfg, user_cfg)
        except (OSError, json.JSONDecodeError) as exc:
            raise GenError(f"配置文件解析失败（{CONFIG_FILE}）：{exc}") from exc
    return cfg


def _http(
    url: str,
    *,
    method: str = "GET",
    headers: Optional[dict[str, str]] = None,
    payload: Optional[dict[str, Any]] = None,
    raw_body: Optional[bytes] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> tuple[int, bytes, str]:
    data = None
    if raw_body is not None:
        data = raw_body
    elif payload is not None:
        data = json.dumps(payload).encode("utf-8")
    last_detail = ""
    for attempt in range(3):
        req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read(), resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            body = b""
            try:
                body = exc.read()
            except Exception:
                pass
            detail = body[:800].decode("utf-8", errors="replace")
            if exc.code == 429 and attempt < 2:
                delay = 8 * (attempt + 1)
                time.sleep(delay)
                last_detail = detail
                continue
            raise GenError(f"HTTP {exc.code} from {url}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise GenError(f"无法连接 {url}：{exc.reason}") from exc
        except TimeoutError as exc:
            raise GenError(f"请求超时（{timeout}s）：{url}") from exc
    raise GenError(f"HTTP 429 from {url}: {last_detail}")


def parse_size(size: str) -> tuple[int, int]:
    match = re.fullmatch(r"\s*(\d{2,5})\s*[xX×]\s*(\d{2,5})\s*", size or "")
    if not match:
        raise GenError(f"无效的尺寸格式：{size!r}，应为 WxH，例如 1024x1024")
    width, height = int(match.group(1)), int(match.group(2))
    if width < 16 or height < 16 or width > 4096 or height > 4096:
        raise GenError(f"尺寸超出支持范围（16~4096）：{width}x{height}")
    return width, height


def slugify(text: str, max_len: int = 50) -> str:
    slug = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", text, flags=re.UNICODE).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return (slug[:max_len] or "image").strip("-") or "image"


def default_output_path(
    prompt: str, seed: int, cfg: dict[str, Any], ext: str = "png"
) -> Path:
    save_dir = (cfg.get("save_dir") or "").strip()
    base = Path(save_dir).expanduser() if save_dir else Path.cwd()
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError:
        base = Path.cwd()
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    filename = f"{APP_NAME}_{timestamp}_{slugify(prompt)}_{seed}.{ext}"
    return base / filename


def mirror_output(path: str, cfg: dict[str, Any]) -> Optional[str]:
    """把生成结果复制一份到 mirror_dir（默认 C:\\Users\\yjq\\Pictures\\codex）。

    复制失败不影响主输出（返回 None），保证"不影响使用"。
    """
    mirror_dir = str(cfg.get("mirror_dir") or "").strip()
    if not mirror_dir:
        return None
    try:
        mirror_dir_path = Path(mirror_dir).expanduser()
        mirror_dir_path.mkdir(parents=True, exist_ok=True)
        dest = mirror_dir_path / Path(path).name
        shutil.copy2(path, dest)
        return str(dest)
    except Exception:  # noqa: BLE001
        return None


def ext_from_content_type(content_type: str) -> str:
    ctype = content_type.lower().split(";")[0].strip()
    if ctype == "image/jpeg" or ctype == "image/jpg":
        return "jpg"
    if ctype == "image/webp":
        return "webp"
    return "png"


def mask_key(key: str) -> str:
    if not key:
        return "(未设置)"
    if len(key) <= 8:
        return "*" * len(key)
    return key[:4] + "*" * (len(key) - 8) + key[-4:]


# ---------------------------------------------------------------- backends

MAX_INIT_BYTES = 20 * 1024 * 1024


def _guess_mime(data: bytes, name: str = "") -> str:
    """按文件头嗅探图片 MIME，嗅探不到时按扩展名兜底。"""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    ext = Path(name).suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(ext, "application/octet-stream")


def probe_image_size(data: bytes, mime: str = "") -> Optional[tuple[int, int]]:
    """不依赖第三方库，从图片文件头解析宽高（PNG / JPEG / WebP）。解析失败返回 None。"""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
            width = int.from_bytes(data[16:20], "big")
            height = int.from_bytes(data[20:24], "big")
            if 1 <= width <= 32768 and 1 <= height <= 32768:
                return width, height
        if data[:3] == b"\xff\xd8\xff" and len(data) >= 12:
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                if i + 4 > len(data):
                    break
                seg_len = int.from_bytes(data[i + 2 : i + 4], "big")
                if (
                    marker
                    in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF)
                    and i + 9 <= len(data)
                ):
                    height = int.from_bytes(data[i + 5 : i + 7], "big")
                    width = int.from_bytes(data[i + 7 : i + 9], "big")
                    if 1 <= width <= 32768 and 1 <= height <= 32768:
                        return width, height
                i += 2 + seg_len
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) >= 30:
            if data[12:16] == b"VP8X":
                width = int.from_bytes(data[24:27], "little") + 1
                height = int.from_bytes(data[27:30], "little") + 1
                return width, height
            if data[12:16] == b"VP8 ":
                width = int.from_bytes(data[26:28], "little") & 0x3FFF
                height = int.from_bytes(data[28:30], "little") & 0x3FFF
                return width, height
            if data[12:16] == b"VP8L" and len(data) >= 25:
                bits = int.from_bytes(data[21:25], "little")
                width = (bits & 0x3FFF) + 1
                height = ((bits >> 14) & 0x3FFF) + 1
                return width, height
    except Exception:  # noqa: BLE001
        return None
    return None


def load_init_image(ref: str) -> tuple[bytes, str, str]:
    """读取图生图参考图，返回 (图片字节, MIME, 建议文件名)。支持本地路径 / http(s) 链接 / data URI。"""
    ref = (ref or "").strip().strip('"')
    if not ref:
        raise GenError("--image 不能为空。")
    name = "reference"
    data: bytes = b""
    mime = ""
    if ref.startswith(("http://", "https://")):
        status, body, ctype = _http(
            ref, headers={"User-Agent": BROWSER_UA}, timeout=HEALTH_TIMEOUT * 3
        )
        if not ctype.lower().startswith("image/"):
            raise GenError(f"--image 指向的内容不是图片（Content-Type: {ctype}）。")
        data = body
        parsed_name = urllib.parse.urlparse(ref).path.rsplit("/", 1)[-1]
        if parsed_name:
            name = parsed_name
        mime = ctype.split(";")[0].strip()
    elif ref.startswith("data:image/"):
        header, _, b64 = ref.partition(",")
        try:
            data = base64.b64decode(b64)
        except Exception as exc:  # noqa: BLE001
            raise GenError(f"data URI 图片解码失败：{exc}") from exc
        mime = header[5:].split(";")[0].strip()
    else:
        path = Path(ref).expanduser()
        if not path.is_file():
            raise GenError(f"找不到图片文件：{path}")
        data = path.read_bytes()
        name = path.name
    if not data:
        raise GenError("图片内容为空。")
    if len(data) > MAX_INIT_BYTES:
        raise GenError(f"图片过大（{len(data) // 1024} KB），上限 {MAX_INIT_BYTES // (1024 * 1024)} MB。")
    mime = mime or _guess_mime(data, name)
    return data, mime, name


def _multipart(
    fields: dict[str, Any], files: list[tuple[str, str, bytes, str]]
) -> tuple[bytes, str]:
    """构造 multipart/form-data 请求体，返回 (body, Content-Type)。"""
    boundary = "----codex" + "".join(random.choices("abcdef0123456789", k=16))
    parts: list[bytes] = []
    for field_name, value in fields.items():
        parts.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{field_name}"'
                f"\r\n\r\n{value}\r\n"
            ).encode("utf-8")
        )
    for field_name, filename, content, ctype in files:
        safe_name = filename.replace('"', "")
        parts.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{field_name}"; '
                f'filename="{safe_name}"\r\nContent-Type: {ctype}\r\n\r\n'
            ).encode("utf-8")
        )
        parts.append(content)
        parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"

def read_first_api_key(text: str) -> str:
    """从 api_keys.txt 文本中读取第一个有效 Key（格式：name:sk-... 或单独 sk-...）。"""
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
    """从模型列表中挑选最好的图像模型：优先非预览、pro > flash > lite、版本号更高。"""
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
            major = int(version.group(1))
            minor = int(version.group(2) or 0)
            total += major * 10 + minor
        return total

    return max(candidates, key=score)


def pick_best_text_model(models: list[str]) -> str:
    """从模型列表中挑选最适合当提示词翻译官的聊天模型（排除图像/音频/翻译等专用模型）。"""
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


def _read_deepseek_credential_from_codex() -> tuple[str, str]:
    """从环境变量或 Codex 配置 ~/.codex/config.toml 读取 DeepSeek 供应商的地址与密钥（不回显密钥）。"""
    base_url = os.environ.get("DEEPSEEK_BASE_URL", "").strip()
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    codex_cfg = Path.home() / ".codex" / "config.toml"
    if codex_cfg.is_file():
        try:
            text = codex_cfg.read_text(encoding="utf-8")
        except OSError:
            return base_url, api_key
        match = re.search(
            r'\[model_providers\.deepseek\][^\[]*?base_url\s*=\s*"([^"]+)"[^\[]*?'
            r'experimental_bearer_token\s*=\s*"([^"]+)"',
            text,
            re.S,
        )
        if match:
            if not base_url:
                base_url = match.group(1).strip().rstrip("/")
            if not api_key:
                api_key = match.group(2).strip()
    return base_url, api_key


def _chat_text(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    max_tokens: int = 4096,
    timeout: int = 120,
) -> str:
    """调用 OpenAI 兼容 /chat/completions，返回助手文本；推理模型思考过长时自动加大上限重试。"""

    def call(tokens: int) -> str:
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": tokens,
            "temperature": 0.8,
            "stream": False,
        }
        _status, body, _ctype = _http(
            f"{base_url.rstrip('/')}/chat/completions",
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": f"{APP_NAME}/0.4",
            },
            payload=payload,
            timeout=timeout,
        )
        data = json.loads(body.decode("utf-8", errors="replace"))
        try:
            content = data["choices"][0]["message"].get("content")
        except (KeyError, IndexError) as exc:
            raise GenError(f"聊天接口返回异常：{str(data)[:300]}") from exc
        return str(content or "").strip()

    text = call(max_tokens)
    if not text:
        text = call(max_tokens * 2)
    return text


def _deepseek_text(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    max_tokens: int = 4096,
) -> str:
    """调用 DeepSeek 接口（优先 /v1/responses，失败时回退 /v1/chat/completions）。"""
    system = "\n\n".join(
        str(m.get("content") or "") for m in messages if m.get("role") == "system"
    )
    user = "\n\n".join(
        str(m.get("content") or "") for m in messages if m.get("role") == "user"
    )
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": f"{APP_NAME}/0.4",
    }

    payload = {
        "model": model,
        "instructions": system,
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": user}],
            }
        ],
        "max_output_tokens": max_tokens,
    }
    try:
        _status, body, _ctype = _http(
            f"{base_url.rstrip('/')}/v1/responses",
            method="POST",
            headers=headers,
            payload=payload,
            timeout=120,
        )
        data = json.loads(body.decode("utf-8", errors="replace"))
        parts: list[str] = []
        for item in data.get("output", []):
            if item.get("type") == "message":
                for chunk in item.get("content", []):
                    if chunk.get("type") == "output_text" and chunk.get("text"):
                        parts.append(str(chunk["text"]))
        if parts:
            return "\n".join(parts).strip()
    except GenError:
        pass

    # 回退到 OpenAI 兼容的 chat/completions
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.8,
        "stream": False,
    }
    _status, body, _ctype = _http(
        f"{base_url.rstrip('/')}/v1/chat/completions",
        method="POST",
        headers=headers,
        payload=payload,
        timeout=120,
    )
    data = json.loads(body.decode("utf-8", errors="replace"))
    try:
        return str(data["choices"][0]["message"].get("content") or "").strip()
    except (KeyError, IndexError) as exc:
        raise GenError(f"DeepSeek 接口返回异常：{str(data)[:300]}") from exc


def _looks_broken(text: str, had_cjk: bool) -> bool:
    """判断翻译结果是否因通道问题变成“问号/没收到消息”之类的废回复。"""
    if not text or not text.strip():
        return True
    if not had_cjk:
        return False
    low = text.lower()
    markers = (
        "问号", "没有看到", "没看到", "无法理解", "没有收到", "没有显示",
        "question mark", "question marks", "didn't understand", "did not understand",
        "didn't come through", "did not come through", "not display", "not come through",
        "only question", "only got question", "string of question",
    )
    return any(m in low for m in markers)


def build_translator_system(lang: str = "zh") -> str:
    """生成提示词翻译官的系统提示词（参考 Gemini 官方提示指南与社区最佳实践）。"""
    if lang == "en":
        return (
            "You are a senior visual prompt engineer. Rewrite the user's image request "
            "into a well-structured prompt for modern image models such as gemini-3-pro-image.\n\n"
            "Hard rules:\n"
            "1. Use natural, descriptive full sentences. Never dump isolated keywords.\n"
            "2. Cover, in order: subject (identity, appearance, outfit, pose, expression) -> "
            "environment (place, background, depth) -> lighting (source, color, contrast, rim light) -> "
            "style/medium (Japanese anime illustration, thick paint, watercolor, photorealism, etc.) -> "
            "composition (aspect ratio, camera angle, rule of thirds, foreground/midground/background) -> "
            "on-image text if any (put exact text in quotes, state position and font style, at most 2-3 phrases).\n"
            "3. Keep every detail the user mentioned; you may add reasonable details to complete the scene.\n"
            "4. Keep the prompt between 80 and 250 words.\n"
            "5. On-image text should be English and placed in quotes.\n"
            "6. Output only the prompt itself: no explanations, no Markdown, no surrounding quotes."
        )
    return (
        "你是一位精通图像生成提示词的资深视觉设计师。请把用户用中文描述的画面需求，"
        "改写成适合 gemini-3-pro-image 等新一代图像模型的生图提示词。\n\n"
        "硬性要求：\n"
        "1. 用自然连贯的完整句子描述，禁止堆砌孤立关键词；Gemini 图像模型喜欢自然描述段落，讨厌关键词清单。\n"
        "2. 按顺序覆盖：主体（身份、外貌、服装、姿态、表情）→ 环境背景（地点、建筑、天气、景深层次）→ "
        "光影（光源、颜色、冷暖对比、轮廓光）→ 风格媒介（日系动漫插画、厚涂、水彩、写实摄影等）→ "
        "构图（画幅比例、机位、居中/三分法、前景中景背景）→ 画面文字（如有，用英文引号标出，"
        "例如 \"Merry Xmas ♡\"，并说明位置与字体风格，最多 2-3 条）。\n"
        "3. 用户提到的每一个细节都必须保留，不许遗漏；可以补充合理细节增强画面完成度。\n"
        "4. 提示词长度控制在 150-500 字之间；内容复杂时宁长勿短。\n"
        "5. 画面中的文字优先使用英文并放进引号。\n"
        "6. 只输出提示词正文：不要解释、不要 Markdown、不要编号列表、不要用引号包裹全文。"
    )


def translate_prompt(
    user_text: str,
    cfg: Optional[dict[str, Any]] = None,
    engine: str = "auto",
    feedback: str = "",
    max_tokens: int = 4096,
) -> dict[str, Any]:
    """提示词翻译官：把用户需求改写成结构化生图提示词。"""
    cfg = cfg if cfg is not None else load_config()
    tr = cfg.get("translator") or {}
    if not isinstance(tr, dict):
        tr = {}
    if engine in ("", "auto"):
        engine = str(tr.get("engine") or "deepseek").strip().lower()
    engine = engine.strip().lower()
    if engine in ("off", "none", "direct", "直传"):
        return {
            "ok": True,
            "engine": "off",
            "engine_used": "off",
            "model": "",
            "original": user_text,
            "rewritten": user_text,
            "fallback": False,
        }
    if engine not in ("deepseek", "gemini"):
        engine = "deepseek"

    lang = str(tr.get("output_lang") or "zh").lower()
    system = build_translator_system(lang)
    user_msg = str(user_text).strip()
    if feedback and str(feedback).strip():
        user_msg += "\n\n【上次生成后发现的问题，请在重写时重点修正】\n" + str(feedback).strip()
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_msg},
    ]
    had_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in user_text)

    if engine == "deepseek":
        ds = tr.get("deepseek") or {}
        if not isinstance(ds, dict):
            ds = {}
        base_url = str(ds.get("base_url") or "").strip().rstrip("/")
        api_key = str(ds.get("api_key") or "").strip()
        model = str(ds.get("model") or "deepseek-v4-flash").strip() or "deepseek-v4-flash"
        if not base_url or not api_key:
            codex_base, codex_key = _read_deepseek_credential_from_codex()
            base_url = base_url or codex_base
            api_key = api_key or codex_key
        if not base_url or not api_key:
            raise GenError(
                "翻译官(deepseek)未配置地址/密钥：请在设置页填写，或改用 gemini 引擎。"
            )
        fallback_reason = ""
        try:
            text = _deepseek_text(base_url, api_key, model, messages, max_tokens)
        except GenError as exc:
            text = ""
            fallback_reason = str(exc)
        if _looks_broken(text, had_cjk):
            info = discover_vertex(cfg)
            gm_model = str((tr.get("gemini") or {}).get("model") or "").strip()
            if not gm_model:
                gm_model = pick_best_text_model(info["models"])
            if not gm_model:
                raise GenError("DeepSeek 通道异常且未找到可用的 Gemini 文本模型，请检查配置。")
            gm_text = _chat_text(info["base_url"], info["api_key"], gm_model, messages, max_tokens)
            return {
                "ok": True,
                "engine": "deepseek",
                "engine_used": "gemini",
                "model": gm_model,
                "original": user_text,
                "rewritten": gm_text,
                "fallback": True,
                "fallback_reason": fallback_reason or "DeepSeek 通道未返回有效中文回复，已自动改用本地 Gemini",
            }
        return {
            "ok": True,
            "engine": "deepseek",
            "engine_used": "deepseek",
            "model": model,
            "original": user_text,
            "rewritten": text,
            "fallback": False,
        }

    # gemini 引擎：走本地 Vertex Proxy 的最佳文本模型
    info = discover_vertex(cfg)
    gm_model = str((tr.get("gemini") or {}).get("model") or "").strip()
    if not gm_model:
        gm_model = pick_best_text_model(info["models"])
    if not gm_model:
        raise GenError("未找到可用的 Gemini 文本模型，请检查 Vertex Proxy 模型列表。")
    text = _chat_text(info["base_url"], info["api_key"], gm_model, messages, max_tokens)
    return {
        "ok": True,
        "engine": "gemini",
        "engine_used": "gemini",
        "model": gm_model,
        "original": user_text,
        "rewritten": text,
        "fallback": False,
    }


def find_vision_bridge(cfg: dict[str, Any]) -> str:
    """定位视觉识别桥接脚本（自动改图检查用）。"""
    tr = cfg.get("translator") or {}
    if not isinstance(tr, dict):
        tr = {}
    cand = str(tr.get("vision_bridge") or "").strip().strip('"')
    if cand and Path(cand).is_file():
        return cand
    roots = [
        Path.home() / ".codex" / "plugins" / "cache" / "deepseek-vision" / "deepseek-vision",
        Path("D:/视觉识别支持的插件/plugins/deepseek-vision"),
    ]
    for root in roots:
        if root.is_dir():
            for p in sorted(root.glob("**/vision_bridge.py"), reverse=True):
                if p.is_file():
                    return str(p)
    return ""


def _vision_says_ok(text: str) -> bool:
    t = str(text or "").strip().strip("。.！! ")
    if len(t) > 24:
        return False
    return any(m in t for m in ("无", "没有", "没问题", "符合", "ok", "okay", "none", "good"))


def _split_issues_lines(text: str) -> list[str]:
    """把视觉返回的问题文本拆成条目列表（去掉编号、符号和"无"）。"""
    _HEADERS = ("人物问题", "人物级问题", "人物级", "背景问题", "背景/细节问题", "背景细节", "背景/细节", "背景")
    out: list[str] = []
    for raw in str(text or "").splitlines():
        line = raw.strip().strip("。.；;，, ")
        if any(line.startswith(h) or line == h for h in _HEADERS):
            continue
        line = re.sub(r"^[\d一二三四五六七八九十]+[\.、．)]\s*", "", line).strip()
        line = re.sub(r"^[-•*]\s*", "", line).strip()
        if not line:
            continue
        if line in ("无", "none", "None", "没有", "没问题"):
            continue
        out.append(line)
    return out


def _count_issues(text: str) -> int:
    return len(_split_issues_lines(text))


def _parse_tiered_issues(text: str) -> dict[str, Any]:
    """把视觉返回的"人物问题/背景问题"两段式文本解析成两类问题。

    解析失败（没有分类标题）时整段按人物级处理，宁严勿松。
    """
    raw = str(text or "").strip()
    character: list[str] = []
    background: list[str] = []
    section = ""
    saw_header = False
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if "人物问题" in line or "人物级" in line or line.startswith("人物"):
            saw_header = True
            section = "char"
            rest = line.split("：", 1)[-1].split(":", 1)[-1].strip()
            if rest and rest not in ("无", "none", "没有"):
                character.append(rest)
            continue
        if "背景问题" in line or "背景/细节" in line or "背景细节" in line or line.startswith("背景"):
            saw_header = True
            section = "bg"
            rest = line.split("：", 1)[-1].split(":", 1)[-1].strip()
            if rest and rest not in ("无", "none", "没有"):
                background.append(rest)
            continue
        if section == "char":
            if line and line not in ("无", "none", "没有"):
                character.append(line)
        elif section == "bg":
            if line and line not in ("无", "none", "没有"):
                background.append(line)
    char_text = "\n".join(character).strip()
    bg_text = "\n".join(background).strip()
    if not saw_header and raw:
        return {"character": raw, "background": "", "parsed": False}
    return {
        "character": char_text,
        "background": bg_text,
        "parsed": saw_header or bool(character or background),
    }


def _fix_issues_text(check: dict[str, Any]) -> str:
    """汇总视觉检查发现的问题（人物级+背景细节），供修正指令使用。"""
    char = str(check.get("character_issues") or "").strip()
    bg = str(check.get("background_issues") or "").strip()
    if char and bg:
        return "人物问题：\n" + char + "\n\n背景问题：\n" + bg
    if char:
        return char
    if bg:
        return bg
    return str(check.get("issues") or "").strip()


def build_fix_instruction(issues: str, max_len: int = 600) -> str:
    """生成最小改动式的局部修正指令：只改问题项，其余一律保持原样。"""
    items = _split_issues_lines(issues)
    if not items:
        return "画面已符合需求，无需修改。"
    body = "\n".join(f"{i}. {it}" for i, it in enumerate(items, 1))
    if len(body) > max_len:
        body = body[:max_len].rsplit("\n", 1)[0]
    return (
        "这是一张需要局部修正的原图。请只修正下面列出的问题，"
        "除此之外画面中的所有内容都必须保持原样、不得改动：\n"
        "（包括人物长相、发色发型、耳机、服饰、姿势、表情、背景、光影、画风、构图）\n"
        "需要修正的问题：\n" + body + "\n\n保持与原图相同的尺寸、风格和整体布局，画面中不要出现任何文字。"
    )


def _finalize_vision_check(issues: str, tiered: bool) -> dict[str, Any]:
    """把视觉返回文本整理成统一结果字典。"""
    result: dict[str, Any] = {
        "ok": True,
        "issues": issues,
        "has_issues": not _vision_says_ok(issues),
    }
    if tiered:
        parts = _parse_tiered_issues(issues)
        char_n = _count_issues(parts["character"])
        bg_n = _count_issues(parts["background"])
        result["character_issues"] = parts["character"]
        result["background_issues"] = parts["background"]
        if parts.get("parsed"):
            result["has_issues"] = (char_n + bg_n) > 0
        result["has_character_issues"] = char_n > 0
        result["issue_counts"] = {"character": char_n, "background": bg_n}
    return result


def _fix_accepted(old_check: dict[str, Any], new_check: dict[str, Any]) -> tuple[bool, str]:
    """保留最佳判定：修正版不能更差，且要有实际改善（人物级错误减少或总数减少）。"""
    if not new_check.get("ok"):
        return True, "修正版未能完成视觉复查，按保留处理"
    if not new_check.get("has_issues"):
        return True, "修正版已通过复查，无遗留问题"
    old_counts = old_check.get("issue_counts") or {
        "character": 0,
        "background": _count_issues(str(old_check.get("issues") or "")),
    }
    new_counts = new_check.get("issue_counts") or {
        "character": 0,
        "background": _count_issues(str(new_check.get("issues") or "")),
    }
    old_total = int(old_counts.get("character", 0)) + int(old_counts.get("background", 0))
    new_total = int(new_counts.get("character", 0)) + int(new_counts.get("background", 0))
    old_char = int(old_counts.get("character", 0))
    new_char = int(new_counts.get("character", 0))
    if new_char > old_char:
        return False, "修正版引入了新的人物级错误"
    if new_char == 0 and new_total <= old_total:
        return True, "修正版已修掉问题且没有新增错误"
    if new_char < old_char:
        return True, "修正版的人物级错误已减少"
    if new_total < old_total:
        return True, "修正版的总问题数已减少"
    return False, "修正版没有实际改善"


def run_vision_check(
    image_path: str,
    user_text: str,
    cfg: dict[str, Any],
    tiered: bool = False,
) -> dict[str, Any]:
    """调用视觉插件检查生成图与用户需求的差距。

    tiered=True 时把问题分为"人物级"与"背景细节"两类返回：
    character_issues / background_issues / has_character_issues。
    """
    bridge = find_vision_bridge(cfg)
    if not bridge:
        return {"ok": False, "reason": "未找到视觉识别桥接脚本，请在设置页填写", "issues": ""}
    if tiered:
        question = (
            "请对照以下用户需求，检查这张图片：\n"
            + str(user_text)[:1500]
            + "\n\n请把发现的问题严格分成两类输出，每类一行标题，下面逐条编号列出：\n"
            "人物问题：只列人物本身缺失或画错的地方（五官、发色发型、耳机、服饰、姿态表情等）\n"
            "背景问题：只列背景/环境/地面/装饰等细节的问题\n"
            "如果某一类没有问题，写：无。如果画面已完全符合需求，两类都写：无。"
        )
    else:
        question = (
            "请对照以下用户需求，检查这张图片：\n"
            + str(user_text)[:1500]
            + "\n\n逐条列出画面中缺失、画错或需要修正的细节。"
            "如果画面已经符合需求，只回答：无"
        )
    # 优先在进程内直接调用视觉桥接函数（中文参数不走命令行，避免 Windows 编码问题）
    try:
        bridge_dir = str(Path(bridge).parent)
        if bridge_dir not in sys.path:
            sys.path.insert(0, bridge_dir)
        import vision_bridge  # type: ignore  # noqa: PLC0415

        vb_cfg = vision_bridge.load_config(None)
        args_ns = argparse.Namespace(backend=None, profile=None, model=None, lang="zh")
        _backend, _profile, _model, result = vision_bridge.run_command(
            "ask", image_path, question, vb_cfg, args_ns
        )
        issues = str(result or "").strip()
        if not issues:
            return {"ok": False, "reason": "视觉检查返回为空", "issues": ""}
        return _finalize_vision_check(issues, tiered)
    except Exception as exc:  # noqa: BLE001
        inproc_error = str(exc)
    # 回退：子进程方式（中文问题用 base64 编码，纯 ASCII 传递）
    b64_question = base64.b64encode(question.encode("utf-8")).decode("ascii")
    ascii_question = (
        "Decode the following base64 text as UTF-8, then use the decoded Chinese text "
        "as the user requirement to check this image. List every missing or wrong detail. "
        "If the image already matches the requirement, answer only: 无\nBASE64:\n" + b64_question
    )
    try:
        proc = subprocess.run(
            [sys.executable, bridge, "ask", image_path, ascii_question, "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=240,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"视觉检查执行失败：{exc}", "issues": ""}
    if proc.returncode != 0:
        return {
            "ok": False,
            "reason": (
                proc.stderr.strip()
                or proc.stdout.strip()[:300]
                or "视觉检查失败"
            ),
            "issues": "",
        }
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "reason": "视觉检查输出无法解析", "issues": ""}
    issues = str(data.get("result") or "").strip()
    if not issues:
        return {"ok": False, "reason": "视觉检查返回为空", "issues": ""}
    return _finalize_vision_check(issues, tiered)


def discover_vertex(cfg: dict[str, Any]) -> dict[str, Any]:
    """读取本地 Vertex Proxy 的端口、密钥与模型列表，返回可用的连接信息。"""
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
        models = [m for m in data.get("models", []) if isinstance(m, str)]

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


def _gen_openai_image(
    base_url: str, api_key: str, model: str, prompt: str, width: int, height: int
) -> bytes:
    """调用任意 OpenAI 兼容的 /images/generations 接口。"""
    base = base_url.rstrip("/")
    payload = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "size": f"{width}x{height}",
    }
    status, body, content_type = _http(
        f"{base}/images/generations",
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": f"{APP_NAME}/0.2",
        },
        payload=payload,
    )
    return _extract_image_from_response(body)


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
        raise GenError(f"图像接口未返回图片：{body[:500]!r}")
    item = items[0]
    b64 = item.get("b64_json") or item.get("image")
    if b64:
        try:
            return base64.b64decode(b64)
        except Exception as exc:
            raise GenError(f"图片 base64 解码失败：{exc}") from exc
    url = item.get("url")
    if url:
        if url.startswith("data:image/"):
            _, b64 = url.split(",", 1)
            return base64.b64decode(b64)
        _, body, content_type = _http(url)
        return body
    raise GenError(f"图像接口返回中缺少图片数据：{body[:500]!r}")


def _decode_sd_webui_image(body: bytes) -> bytes:
    """从 SD WebUI 响应中提取第一张图片字节。"""
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GenError(f"SD WebUI 返回了无法解析的内容：{body[:300]!r}") from exc
    images = data.get("images") or []
    if not images:
        raise GenError(f"SD WebUI 未返回图片：{body[:500]!r}")
    try:
        return base64.b64decode(images[0])
    except Exception as exc:  # noqa: BLE001
        raise GenError(f"SD WebUI 图片 base64 解码失败：{exc}") from exc


def gen_pollinations(
    cfg: dict[str, Any], prompt: str, width: int, height: int, seed: int, model: str
) -> tuple[bytes, str]:
    bc = cfg.get("pollinations", {})
    base = (bc.get("base_url") or DEFAULT_CONFIG["pollinations"]["base_url"]).rstrip("/")
    quoted = urllib.parse.quote(prompt, safe="")
    params: dict[str, Any] = {
        "width": width,
        "height": height,
        "seed": seed,
        "nologo": "true",
        "private": "true",
    }
    model = model or (bc.get("model") or "").strip()
    if model:
        params["model"] = model
    url = f"{base}/{quoted}?{urllib.parse.urlencode(params)}"
    status, body, content_type = _http(url, headers={"User-Agent": BROWSER_UA})
    if not content_type.lower().startswith("image/"):
        raise GenError(f"Pollinations 返回了非图片内容（{content_type}）：{body[:300]!r}")
    return body, content_type


def gen_siliconflow(
    cfg: dict[str, Any], prompt: str, width: int, height: int, model: str
) -> bytes:
    bc = cfg.get("siliconflow", {})
    base = (bc.get("base_url") or DEFAULT_CONFIG["siliconflow"]["base_url"]).rstrip("/")
    api_key = (bc.get("api_key") or "").strip()
    if not api_key:
        raise GenError(
            "siliconflow 未配置 api_key。请在 ~/.deepseek-imagegen/config.json 的 "
            "siliconflow.api_key 中填写（https://cloud.siliconflow.cn 申请）。"
        )
    model = model or (bc.get("model") or "").strip()
    if not model:
        raise GenError("siliconflow 未配置 model，请在 config.json 中填写。")
    return _gen_openai_image(base, api_key, model, prompt, width, height)


def gen_vertex(cfg: dict[str, Any], prompt: str, width: int, height: int, model: str) -> bytes:
    info = discover_vertex(cfg)
    model = model or info["model"]
    return _gen_openai_image(info["base_url"], info["api_key"], model, prompt, width, height)


def gen_vertex_img2img(
    cfg: dict[str, Any],
    prompt: str,
    width: int,
    height: int,
    model: str,
    image_bytes: bytes,
    image_mime: str,
    image_name: str,
) -> bytes:
    """调用本地 Vertex Proxy 的 OpenAI 兼容 /images/edits 接口做图生图。"""
    info = discover_vertex(cfg)
    model = model or info["model"]
    body, content_type = _multipart(
        {"model": model, "prompt": prompt, "n": "1", "size": f"{width}x{height}"},
        [("image", image_name, image_bytes, image_mime)],
    )
    status, resp_body, resp_ctype = _http(
        f"{info['base_url'].rstrip('/')}/images/edits",
        method="POST",
        headers={
            "Authorization": f"Bearer {info['api_key']}",
            "Content-Type": content_type,
            "User-Agent": f"{APP_NAME}/0.3",
        },
        raw_body=body,
    )
    return _extract_image_from_response(resp_body)


def gen_sd_webui(
    cfg: dict[str, Any],
    prompt: str,
    negative: str,
    width: int,
    height: int,
    seed: int,
    steps: int,
    cfg_scale: float,
    sampler: str,
) -> bytes:
    bc = cfg.get("sd_webui", {})
    base = (bc.get("base_url") or DEFAULT_CONFIG["sd_webui"]["base_url"]).rstrip("/")
    payload = {
        "prompt": prompt,
        "negative_prompt": negative,
        "width": width,
        "height": height,
        "seed": seed,
        "steps": steps,
        "cfg_scale": cfg_scale,
        "sampler_name": sampler,
    }
    status, body, content_type = _http(
        f"{base}/sdapi/v1/txt2img",
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": f"{APP_NAME}/0.1"},
        payload=payload,
    )
    return _decode_sd_webui_image(body)


def gen_sd_webui_img2img(
    cfg: dict[str, Any],
    prompt: str,
    negative: str,
    width: int,
    height: int,
    seed: int,
    steps: int,
    cfg_scale: float,
    sampler: str,
    init_image: tuple[bytes, str, str],
    denoising_strength: float,
) -> bytes:
    """调用 SD WebUI 的 /sdapi/v1/img2img 接口做图生图。"""
    bc = cfg.get("sd_webui", {})
    base = (bc.get("base_url") or DEFAULT_CONFIG["sd_webui"]["base_url"]).rstrip("/")
    payload = {
        "init_images": [base64.b64encode(init_image[0]).decode("ascii")],
        "prompt": prompt,
        "negative_prompt": negative,
        "width": width,
        "height": height,
        "seed": seed,
        "steps": steps,
        "cfg_scale": cfg_scale,
        "sampler_name": sampler,
        "denoising_strength": denoising_strength,
        "resize_mode": 1,  # 1 = Crop and resize（优先按目标尺寸裁剪，避免变形）
    }
    status, body, content_type = _http(
        f"{base}/sdapi/v1/img2img",
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": f"{APP_NAME}/0.3"},
        payload=payload,
    )
    return _decode_sd_webui_image(body)


def _comfyui_workflow(
    checkpoint: str,
    prompt: str,
    negative: str,
    width: int,
    height: int,
    seed: int,
    steps: int,
    cfg: float,
    sampler: str,
    scheduler: str,
    init_image: str = "",
    denoise: float = 1.0,
) -> dict[str, Any]:
    if init_image:
        latent_node = {
            "class_type": "VaeEncode",
            "inputs": {"pixels": ["10", 0], "vae": ["4", 2]},
        }
    else:
        latent_node = {
            "class_type": "EmptyLatentImage",
            "inputs": {"batch_size": 1, "height": height, "width": width},
        }
    return {
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "cfg": cfg,
                "denoise": denoise,
                "latent_image": ["5", 0],
                "model": ["4", 0],
                "negative": ["7", 0],
                "positive": ["6", 0],
                "sampler_name": sampler,
                "scheduler": scheduler,
                "seed": seed,
                "steps": steps,
            },
        },
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}},
        "5": latent_node,
        "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 1], "text": prompt}},
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["4", 1], "text": negative},
        },
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": APP_NAME, "images": ["8", 0]},
        },
        **(
            {"10": {"class_type": "LoadImage", "inputs": {"image": init_image}}}
            if init_image
            else {}
        ),
    }


def comfyui_checkpoints(cfg: dict[str, Any]) -> list[str]:
    bc = cfg.get("comfyui", {})
    base = (bc.get("base_url") or DEFAULT_CONFIG["comfyui"]["base_url"]).rstrip("/")
    status, body, content_type = _http(
        f"{base}/object_info/CheckpointLoaderSimple", timeout=HEALTH_TIMEOUT
    )
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GenError(f"ComfyUI 返回了无法解析的内容：{body[:300]!r}") from exc
    info = data.get("CheckpointLoaderSimple", {})
    ckpt_input = info.get("input", {}).get("required", {}).get("ckpt_name", [])
    return list(ckpt_input[0]) if ckpt_input and isinstance(ckpt_input[0], list) else []


def upload_comfyui_image(
    cfg: dict[str, Any], data: bytes, filename: str, mime: str
) -> str:
    """把图生图参考图上传到 ComfyUI 的 input 目录，返回服务端文件名。"""
    bc = cfg.get("comfyui", {})
    base = (bc.get("base_url") or DEFAULT_CONFIG["comfyui"]["base_url"]).rstrip("/")
    upload_name = f"{APP_NAME}_{int(time.time())}_{filename}"
    upload_body, upload_ctype = _multipart({}, [("image", upload_name, data, mime)])
    status, upload_resp, upload_ct = _http(
        f"{base}/upload/image",
        method="POST",
        headers={"Content-Type": upload_ctype, "User-Agent": f"{APP_NAME}/0.3"},
        raw_body=upload_body,
    )
    try:
        upload_info = json.loads(upload_resp.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GenError(f"ComfyUI 上传图片失败：{upload_resp[:300]!r}") from exc
    server_name = str(upload_info.get("name") or "").strip()
    if not server_name:
        raise GenError(f"ComfyUI 上传图片未返回文件名：{upload_resp[:300]!r}")
    return server_name


def gen_comfyui(
    cfg: dict[str, Any],
    prompt: str,
    negative: str,
    width: int,
    height: int,
    seed: int,
    steps: int,
    cfg_scale: float,
    sampler: str,
    scheduler: str,
    init_image: Optional[tuple[bytes, str, str]] = None,
    denoise: float = 1.0,
) -> bytes:
    bc = cfg.get("comfyui", {})
    base = (bc.get("base_url") or DEFAULT_CONFIG["comfyui"]["base_url"]).rstrip("/")
    checkpoint = (bc.get("checkpoint") or "").strip()
    if not checkpoint:
        available = comfyui_checkpoints(cfg)
        if not available:
            raise GenError(
                "ComfyUI 没有可用 checkpoint。请先在 ComfyUI 中安装模型，"
                "或在 config.json 的 comfyui.checkpoint 中指定名称。"
            )
        checkpoint = available[0]
    init_name = ""
    if init_image is not None:
        init_name = upload_comfyui_image(cfg, *init_image)
    workflow = _comfyui_workflow(
        checkpoint,
        prompt,
        negative,
        width,
        height,
        seed,
        steps,
        cfg_scale,
        sampler,
        scheduler,
        init_image=init_name,
        denoise=denoise,
    )
    client_id = f"{APP_NAME}-{os.getpid()}-{int(time.time())}"
    status, body, content_type = _http(
        f"{base}/prompt",
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": f"{APP_NAME}/0.1"},
        payload={"prompt": workflow, "client_id": client_id},
    )
    try:
        submitted = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GenError(f"ComfyUI 提交任务失败：{body[:500]!r}") from exc
    prompt_id = submitted.get("prompt_id")
    if not prompt_id:
        raise GenError(f"ComfyUI 未返回 prompt_id：{body[:500]!r}")

    deadline = time.monotonic() + DEFAULT_TIMEOUT
    while True:
        if time.monotonic() > deadline:
            raise GenError("ComfyUI 生成超时，请稍后重试或加大 steps 限制。")
        time.sleep(2)
        try:
            status, body, content_type = _http(
                f"{base}/history/{prompt_id}", timeout=HEALTH_TIMEOUT
            )
            history = json.loads(body.decode("utf-8"))
        except (GenError, json.JSONDecodeError):
            continue
        entry = (history.get(prompt_id) or {}) if isinstance(history, dict) else {}
        outputs = entry.get("outputs") or {}
        save_node = outputs.get("9") or outputs.get(str(len(workflow)))
        if not save_node:
            continue
        images = save_node.get("images") or []
        if not images:
            raise GenError("ComfyUI 任务完成但没有输出图片。")
        image = images[0]
        query = urllib.parse.urlencode(
            {
                "filename": image.get("filename", ""),
                "subfolder": image.get("subfolder", ""),
                "type": image.get("type", "output"),
            }
        )
        status, body, content_type = _http(f"{base}/view?{query}")
        return body


def resolve_backend(name: str, cfg: dict[str, Any]) -> str:
    key = (name or "auto").strip().lower()
    backend = BACKEND_ALIASES.get(key)
    if key == "auto" or backend is None:
        backend = (cfg.get("default_backend") or "pollinations").strip().lower()
    backend = BACKEND_ALIASES.get(backend, backend)
    if backend not in ("pollinations", "siliconflow", "vertex", "sd-webui", "comfyui"):
        raise GenError(
            f"未知后端：{name!r}。可选：vertex / pollinations / siliconflow / sd-webui / comfyui"
        )
    return backend


def generate_image(
    prompt: str,
    *,
    backend: str = "auto",
    out: Optional[str] = None,
    size: str = "",
    seed: Optional[int] = None,
    negative: str = "",
    steps: Optional[int] = None,
    cfg: Optional[float] = None,
    model: str = "",
    init_image: Optional[str] = None,
    denoise: Optional[float] = None,
) -> dict[str, Any]:
    if not prompt or not prompt.strip():
        raise GenError("提示词不能为空。")
    cfg_all = load_config()
    backend = resolve_backend(backend, cfg_all)
    if denoise is not None and not (0 < denoise <= 1):
        raise GenError("--denoise 必须是 0~1 之间的小数（默认 0.6）。")

    init_data: Optional[tuple[bytes, str, str]] = None
    if init_image:
        init_data = load_init_image(init_image)
        if size:
            width, height = parse_size(size)
        else:
            probed = probe_image_size(init_data[0], init_data[1])
            width, height = probed or parse_size("1024x1024")
    else:
        width, height = parse_size(size or "1024x1024")
    seed = seed if seed is not None else random.randint(0, 2**31 - 1)

    out_ext = "png"
    denoise_used: Optional[float] = None
    if backend == "pollinations":
        if init_data is not None:
            raise GenError("pollinations 不支持图生图，请使用 --backend vertex / sd-webui / comfyui。")
        data, content_type = gen_pollinations(cfg_all, prompt.strip(), width, height, seed, model)
        out_ext = ext_from_content_type(content_type)
    elif backend == "siliconflow":
        if init_data is not None:
            raise GenError("siliconflow 不支持图生图，请使用 --backend vertex / sd-webui / comfyui。")
        data = gen_siliconflow(cfg_all, prompt.strip(), width, height, model)
    elif backend == "vertex":
        if init_data is not None:
            denoise_used = denoise if denoise is not None else 0.6
            data = gen_vertex_img2img(
                cfg_all,
                prompt.strip(),
                width,
                height,
                model,
                init_data[0],
                init_data[1],
                init_data[2],
            )
        else:
            data = gen_vertex(cfg_all, prompt.strip(), width, height, model)
    elif backend == "sd-webui":
        bc = cfg_all.get("sd_webui", {})
        steps_n = int(steps if steps is not None else bc.get("steps", 28))
        cfg_n = float(cfg if cfg is not None else bc.get("cfg_scale", 7))
        sampler = str(bc.get("sampler_name", "Euler a"))
        if init_data is not None:
            denoise_used = denoise if denoise is not None else float(
                bc.get("denoising_strength", DEFAULT_CONFIG["sd_webui"]["denoising_strength"])
            )
            data = gen_sd_webui_img2img(
                cfg_all,
                prompt.strip(),
                negative.strip(),
                width,
                height,
                seed,
                steps_n,
                cfg_n,
                sampler,
                init_data,
                denoise_used,
            )
        else:
            data = gen_sd_webui(
                cfg_all,
                prompt.strip(),
                negative.strip(),
                width,
                height,
                seed,
                steps_n,
                cfg_n,
                sampler,
            )
    else:
        bc = cfg_all.get("comfyui", {})
        steps_n = int(steps if steps is not None else bc.get("steps", 28))
        cfg_n = float(cfg if cfg is not None else bc.get("cfg", 7))
        sampler = str(bc.get("sampler_name", "euler"))
        scheduler = str(bc.get("scheduler", "normal"))
        if init_data is not None:
            denoise_used = denoise if denoise is not None else float(
                bc.get("denoise", DEFAULT_CONFIG["comfyui"]["denoise"])
            )
            data = gen_comfyui(
                cfg_all,
                prompt.strip(),
                negative.strip(),
                width,
                height,
                seed,
                steps_n,
                cfg_n,
                sampler,
                scheduler,
                init_image=init_data,
                denoise=denoise_used,
            )
        else:
            data = gen_comfyui(
                cfg_all,
                prompt.strip(),
                negative.strip(),
                width,
                height,
                seed,
                steps_n,
                cfg_n,
                sampler,
                scheduler,
            )

    if out:
        out_path = Path(out).expanduser()
        if out_path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
            # 视为输出目录，自动生成文件名
            out_path = out_path / default_output_path(prompt, seed, cfg_all, ext=out_ext).name
    else:
        out_path = default_output_path(prompt, seed, cfg_all, ext=out_ext)
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
    except FileExistsError as exc:
        raise GenError(
            f"输出路径不可用：{out_path.parent} 位置存在同名文件，请更换 --out 路径。"
        ) from exc
    out_path.write_bytes(data)
    result = {
        "ok": True,
        "backend": backend,
        "path": str(out_path),
        "seed": seed,
        "size": f"{width}x{height}",
        "bytes": len(data),
    }
    if init_data is not None:
        result["init_image"] = init_image
        result["denoise"] = denoise_used
    mirror = mirror_output(str(out_path), cfg_all)
    if mirror:
        result["mirror_path"] = mirror
    return result


# ------------------------------------------------ translator + auto-fix

def generate_with_translator(
    prompt: str,
    *,
    backend: str = "auto",
    out: Optional[str] = None,
    size: str = "",
    seed: Optional[int] = None,
    negative: str = "",
    steps: Optional[int] = None,
    cfg: Optional[float] = None,
    model: str = "",
    init_image: Optional[str] = None,
    denoise: Optional[float] = None,
    translator: str = "auto",
    auto_fix: Optional[bool] = None,
    fix_mode: Optional[str] = None,
    keep_best: Optional[bool] = None,
) -> dict[str, Any]:
    """生成图片：可选先用翻译官改写提示词，再按需自动看图改图。"""
    cfg_all = load_config()
    tr = cfg_all.get("translator") or {}
    if not isinstance(tr, dict):
        tr = {}
    engine = str(translator or "auto")
    if engine in ("", "auto"):
        engine = str(tr.get("engine") or "deepseek")
    tr_enabled = engine.strip().lower() not in ("off", "none", "direct", "直传")
    tr_info: dict[str, Any] = {
        "ok": True,
        "engine": "off",
        "engine_used": "off",
        "model": "",
        "original": prompt,
        "rewritten": prompt,
        "fallback": False,
    }
    if tr_enabled:
        tr_info = translate_prompt(prompt, cfg=cfg_all, engine=engine)
        final_prompt = tr_info.get("rewritten") or prompt
    else:
        final_prompt = prompt
    fix_engine = engine if engine.strip().lower() not in ("off", "none", "direct", "直传") else str(
        tr.get("engine") or "deepseek"
    )

    kwargs = dict(
        backend=backend,
        out=out,
        size=size,
        seed=seed,
        negative=negative,
        steps=steps,
        cfg=cfg,
        model=model,
        init_image=init_image,
        denoise=denoise,
    )
    result = generate_image(final_prompt, **kwargs)
    result["translator"] = tr_info
    result["prompt_used"] = final_prompt

    fix_wanted = auto_fix if auto_fix is not None else bool(tr.get("auto_fix", False))
    fix_mode_val = (fix_mode or tr.get("fix_mode") or "edit").strip().lower()
    if fix_mode_val not in ("edit", "redraw"):
        fix_mode_val = "edit"
    keep_best_val = keep_best if keep_best is not None else bool(tr.get("fix_keep_best", True))
    max_rounds = 0
    try:
        max_rounds = max(0, int(tr.get("max_fix_rounds", 1) or 0))
    except (TypeError, ValueError):
        max_rounds = 1
    history: list[dict[str, Any]] = [
        {"round": 0, "path": result.get("path", ""), "prompt": final_prompt}
    ]
    warnings: list[str] = []
    fix_out = None
    if out:
        _out_path = Path(out).expanduser()
        if _out_path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            fix_out = _out_path
    if (
        fix_wanted
        and max_rounds > 0
        and resolve_backend(backend, cfg_all) == "vertex"
    ):
        current_path = result.get("path", "")
        reverted = False
        for i in range(max_rounds):
            if not current_path or not os.path.isfile(current_path):
                warnings.append(f"自动改图第 {i + 1} 轮跳过：找不到生成图片。")
                break
            check = run_vision_check(current_path, prompt, cfg_all, tiered=True)
            if not check.get("ok"):
                warnings.append("自动看图检查未完成：" + str(check.get("reason") or ""))
                break
            issues = str(check.get("issues") or "")
            history[-1]["issues"] = issues
            if "character_issues" in check:
                history[-1]["character_issues"] = check.get("character_issues") or ""
                history[-1]["background_issues"] = check.get("background_issues") or ""
            if not check.get("has_issues", True):
                break
            if fix_mode_val == "redraw" and not check.get("has_character_issues"):
                warnings.append(
                    "画面仅有背景/细节问题（"
                    + str(check.get("background_issues") or issues)
                    + "），不值得整图重画，已保留当前图片。"
                )
                break
            if fix_mode_val == "edit":
                # 图生图局部修正：用当前图 + 最小改动指令，其余保持原样
                instruction = build_fix_instruction(_fix_issues_text(check))
                round_kwargs = dict(kwargs)
                round_kwargs.pop("size", None)  # 自动沿用原图尺寸
                if fix_out is not None:
                    round_kwargs["out"] = str(
                        fix_out.with_name(f"{fix_out.stem}-fix{i + 1}{fix_out.suffix}")
                    )
                round_kwargs["init_image"] = current_path
                round_kwargs["backend"] = "vertex"
                next_result = generate_image(instruction, **round_kwargs)
                used_prompt = instruction
            else:
                # 整图重画（老方式）：翻译官根据反馈重写提示词再生成
                fb = translate_prompt(
                    prompt, cfg=cfg_all, engine=fix_engine, feedback=_fix_issues_text(check)
                )
                new_prompt = fb.get("rewritten") or final_prompt
                round_kwargs = dict(kwargs)
                if fix_out is not None:
                    round_kwargs["out"] = str(
                        fix_out.with_name(f"{fix_out.stem}-fix{i + 1}{fix_out.suffix}")
                    )
                next_result = generate_image(new_prompt, **round_kwargs)
                used_prompt = new_prompt
            entry: dict[str, Any] = {
                "round": i + 1,
                "path": next_result.get("path", ""),
                "prompt": used_prompt,
                "issues": issues,
            }
            if fix_mode_val == "edit":
                entry["translator"] = None
            else:
                entry["translator"] = fb
            if keep_best_val and next_result.get("ok") and next_result.get("path"):
                recheck = run_vision_check(next_result["path"], prompt, cfg_all, tiered=True)
                accepted, why = _fix_accepted(check, recheck)
                entry["recheck_issues"] = str(recheck.get("issues") or "")
                if not accepted:
                    entry["verdict"] = "reverted"
                    entry["reason"] = why
                    warnings.append(f"第 {i + 1} 轮修正版更差（{why}），已自动退回上一版。")
                    history.append(entry)
                    reverted = True
                    break
                entry["verdict"] = "kept"
            else:
                entry["verdict"] = "kept"
            history.append(entry)
            result = next_result
            current_path = next_result.get("path", "")
        result["auto_fix"] = {
            "ok": True,
            "rounds": len(history) - 1,
            "fix_mode": fix_mode_val,
            "keep_best": keep_best_val,
            "reverted": reverted,
            "history": history,
        }
        last_round = history[-1]
        if reverted and len(history) >= 2:
            last_round = history[-2]
        result["translator"] = last_round.get("translator") or tr_info
        result["prompt_used"] = last_round.get("prompt") or final_prompt
    if warnings:
        result["warnings"] = warnings
    return result


# ---------------------------------------------------------------- commands

def cmd_generate(args: argparse.Namespace) -> dict[str, Any]:
    return generate_with_translator(
        args.prompt,
        backend=args.backend,
        out=args.out,
        size=args.size,
        seed=args.seed,
        negative=args.negative,
        steps=args.steps,
        cfg=args.cfg,
        model=args.model,
        init_image=args.image or None,
        denoise=args.denoise,
        translator=getattr(args, "translator", "auto"),
        auto_fix=getattr(args, "auto_fix", None),
        fix_mode=None if getattr(args, "fix_mode", None) in (None, "", "auto") else args.fix_mode,
        keep_best=getattr(args, "keep_best", None),
    )


def cmd_translate(args: argparse.Namespace) -> dict[str, Any]:
    result = translate_prompt(
        args.prompt,
        engine=getattr(args, "engine", "auto"),
        feedback=getattr(args, "feedback", ""),
    )
    result["ok"] = True
    return result


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


def cmd_doctor(args: argparse.Namespace) -> dict[str, Any]:
    cfg = load_config()
    checks: list[dict[str, Any]] = []

    def check_pollinations(c: dict[str, Any]) -> None:
        bc = c.get("pollinations", {})
        base = (bc.get("base_url") or DEFAULT_CONFIG["pollinations"]["base_url"]).rstrip("/")
        try:
            _http(base, timeout=HEALTH_TIMEOUT, headers={"User-Agent": BROWSER_UA})
        except GenError as exc:
            if "403" in str(exc):
                raise GenError(
                    "服务可达但被 Cloudflare 拦截（403），可尝试更换网络或使用本地后端"
                ) from exc
            raise

    def check_siliconflow(c: dict[str, Any]) -> None:
        key = (c.get("siliconflow", {}).get("api_key") or "").strip()
        if not key:
            raise GenError("未配置 api_key（config.json -> siliconflow.api_key）")

    def check_vertex(c: dict[str, Any]) -> None:
        info = discover_vertex(c)
        status, body, content_type = _http(
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
        vertex_info_holder["info"] = info
        vertex_info_holder["count"] = len(ids)

    def check_sd_webui(c: dict[str, Any]) -> None:
        bc = c.get("sd_webui", {})
        base = (bc.get("base_url") or DEFAULT_CONFIG["sd_webui"]["base_url"]).rstrip("/")
        _http(f"{base}/sdapi/v1/sd-models", timeout=HEALTH_TIMEOUT)

    def check_comfyui(c: dict[str, Any]) -> None:
        bc = c.get("comfyui", {})
        base = (bc.get("base_url") or DEFAULT_CONFIG["comfyui"]["base_url"]).rstrip("/")
        _http(f"{base}/system_stats", timeout=HEALTH_TIMEOUT)

    vertex_info_holder: dict[str, Any] = {}
    checks.append(_health_check("pollinations", check_pollinations, cfg))
    checks.append(_health_check("siliconflow", check_siliconflow, cfg))
    vertex_check = _health_check("vertex", check_vertex, cfg)
    if vertex_info_holder.get("info"):
        vertex_check["best_model"] = vertex_info_holder["info"]["model"]
        vertex_check["model_count"] = vertex_info_holder["count"]
    checks.append(vertex_check)
    checks.append(_health_check("sd-webui", check_sd_webui, cfg))
    checks.append(_health_check("comfyui", check_comfyui, cfg))
    return {
        "ok": any(check["ok"] for check in checks),
        "config_file": str(CONFIG_FILE),
        "config_exists": CONFIG_FILE.exists(),
        "default_backend": cfg.get("default_backend", "pollinations"),
        "checks": checks,
    }


def cmd_config(args: argparse.Namespace) -> dict[str, Any]:
    cfg = load_config()
    safe = json.loads(json.dumps(cfg))
    for section in ("siliconflow", "vertex"):
        key = safe.get(section, {}).get("api_key", "")
        if key:
            safe[section]["api_key"] = mask_key(key)
    return {
        "config_file": str(CONFIG_FILE),
        "config_exists": CONFIG_FILE.exists(),
        "config": safe,
    }


def cmd_list_models(args: argparse.Namespace) -> dict[str, Any]:
    cfg = load_config()
    result: dict[str, Any] = {"ok": True, "models": {}}

    try:
        bc = cfg.get("sd_webui", {})
        base = (bc.get("base_url") or DEFAULT_CONFIG["sd_webui"]["base_url"]).rstrip("/")
        status, body, content_type = _http(f"{base}/sdapi/v1/sd-models", timeout=HEALTH_TIMEOUT)
        models = json.loads(body.decode("utf-8"))
        result["models"]["sd-webui"] = [
            {"title": m.get("title"), "name": m.get("model_name")} for m in models
        ]
    except (GenError, json.JSONDecodeError) as exc:
        result["models"]["sd-webui"] = f"不可用：{exc}"

    try:
        result["models"]["comfyui"] = comfyui_checkpoints(cfg)
    except GenError as exc:
        result["models"]["comfyui"] = f"不可用：{exc}"

    result["models"]["siliconflow"] = (
        "模型列表请在 https://cloud.siliconflow.cn 查看（常用：black-forest-labs/FLUX.1-schnell）"
    )
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
        print(f"尺寸：{result['size']}  种子：{result['seed']}")
        if result.get("init_image"):
            print(
                f"图生图：原图 {result['init_image']}"
                + (f"  去噪强度：{result.get('denoise')}" if result.get("denoise") else "")
            )
    elif "checks" in result:
        print(
            f"配置文件：{result['config_file']}"
            f"（{'存在' if result['config_exists'] else '不存在，使用默认配置'}）"
        )
        print(f"默认后端：{result['default_backend']}")
        for check in result["checks"]:
            print(f"  [{'OK' if check['ok'] else 'FAIL'}] {check['backend']}: {check['message']}")
    elif "models" in result:
        for name, models in result["models"].items():
            print(f"[{name}]")
            if isinstance(models, list):
                for model in models[:20]:
                    if isinstance(model, dict):
                        print(f"  {model.get('title') or model.get('name')}")
                    else:
                        print(f"  {model}")
            else:
                print(f"  {models}")
    elif "config" in result:
        print(f"配置文件：{result['config_file']}")
        print(json.dumps(result["config"], ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="image_gen.py",
        description="DeepSeek ImageGen 桥接脚本：生成图片并保存为 PNG。",
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON（机器可读）")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="生成图片")
    gen.add_argument("prompt", help="提示词")
    gen.add_argument(
        "--backend", default="auto", help="vertex / pollinations / siliconflow / sd-webui / comfyui"
    )
    gen.add_argument("--out", help="输出文件路径")
    gen.add_argument("--size", default="", help="分辨率，如 1024x1024（图生图省略时自动取原图尺寸）")
    gen.add_argument("--seed", type=int, default=None, help="随机种子")
    gen.add_argument("--negative", default="", help="负面提示词（sd-webui / comfyui）")
    gen.add_argument("--steps", type=int, default=None, help="采样步数（sd-webui / comfyui）")
    gen.add_argument("--cfg", type=float, default=None, help="引导强度（sd-webui / comfyui）")
    gen.add_argument("--model", default="", help="模型（pollinations / siliconflow）")
    gen.add_argument(
        "--image",
        default="",
        help="参考图片（图生图）：本地路径或 http(s) 链接（vertex / sd-webui / comfyui）",
    )
    gen.add_argument("--denoise", type=float, default=None, help="去噪强度 0~1（图生图，默认 0.6）")
    gen.add_argument(
        "--translator",
        default="auto",
        choices=["auto", "deepseek", "gemini", "off"],
        help="提示词翻译官：deepseek(默认) / gemini / off(直传) / auto(跟随配置)",
    )
    fix_group = gen.add_mutually_exclusive_group()
    fix_group.add_argument(
        "--auto-fix",
        dest="auto_fix",
        action="store_true",
        default=None,
        help="开启自动看图改图（生成后视觉检查，缺失细节自动局部修正重试）",
    )
    fix_group.add_argument(
        "--no-auto-fix",
        dest="auto_fix",
        action="store_false",
        help="关闭自动看图改图",
    )
    gen.add_argument(
        "--fix-mode",
        dest="fix_mode",
        default=None,
        choices=["auto", "edit", "redraw"],
        help="自动改图方式：edit 局部小修(默认，推荐) / redraw 整图重画 / auto 跟随配置",
    )
    keep_group = gen.add_mutually_exclusive_group()
    keep_group.add_argument(
        "--keep-best",
        dest="keep_best",
        action="store_true",
        default=None,
        help="保留最佳：修正版更差时自动退回上一版（默认开启）",
    )
    keep_group.add_argument(
        "--no-keep-best",
        dest="keep_best",
        action="store_false",
        help="关闭保留最佳",
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

    webui_parser = sub.add_parser("webui", help="启动本地可视化设置页面")
    webui_parser.add_argument("--host", default="127.0.0.1")
    webui_parser.add_argument("--port", type=int, default=8766)

    for name, help_text in (
        ("doctor", "诊断各后端连通性"),
        ("config", "查看当前生效配置"),
        ("list-models", "查看本地后端可用模型"),
    ):
        sub_parser = sub.add_parser(name, help=help_text)
        sub_parser.add_argument("--json", action="store_true", help="输出 JSON（机器可读）")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
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
        elif args.command == "webui":
            import webui  # type: ignore  # noqa: PLC0415

            return webui.serve(host=args.host, port=args.port)
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
