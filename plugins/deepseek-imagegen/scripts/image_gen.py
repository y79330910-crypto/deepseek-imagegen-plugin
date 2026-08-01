#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepSeek ImageGen bridge.

给 Codex 中的 DeepSeek 等纯文本模型提供图像生成能力：
把提示词发给可配置的图像后端，保存生成的 PNG 并返回路径。

后端：
  pollinations   免费公共 API，无需 Key（默认）
  siliconflow    OpenAI 兼容图像接口（FLUX 等），需要 api_key
  vertex         本地 Vertex Proxy（OpenAI 兼容，自动读取端口/密钥/模型列表）
  sd-webui       本地 Stable Diffusion WebUI / Forge（http://127.0.0.1:7860）
  comfyui        本地 ComfyUI（http://127.0.0.1:8188）

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
    },
    "comfyui": {
        "base_url": "http://127.0.0.1:8188",
        "checkpoint": "",
        "sampler_name": "euler",
        "scheduler": "normal",
        "steps": 28,
        "cfg": 7,
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
    timeout: int = DEFAULT_TIMEOUT,
) -> tuple[int, bytes, str]:
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
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
        raise GenError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise GenError(f"无法连接 {url}：{exc.reason}") from exc
    except TimeoutError as exc:
        raise GenError(f"请求超时（{timeout}s）：{url}") from exc


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
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GenError(f"SD WebUI 返回了无法解析的内容：{body[:300]!r}") from exc
    images = data.get("images") or []
    if not images:
        raise GenError(f"SD WebUI 未返回图片：{body[:500]!r}")
    try:
        return base64.b64decode(images[0])
    except Exception as exc:
        raise GenError(f"SD WebUI 图片 base64 解码失败：{exc}") from exc


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
) -> dict[str, Any]:
    return {
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "cfg": cfg,
                "denoise": 1.0,
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
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {"batch_size": 1, "height": height, "width": width},
        },
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
    workflow = _comfyui_workflow(
        checkpoint, prompt, negative, width, height, seed, steps, cfg_scale, sampler, scheduler
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
) -> dict[str, Any]:
    if not prompt or not prompt.strip():
        raise GenError("提示词不能为空。")
    cfg_all = load_config()
    backend = resolve_backend(backend, cfg_all)
    width, height = parse_size(size or "1024x1024")
    seed = seed if seed is not None else random.randint(0, 2**31 - 1)

    out_ext = "png"
    if backend == "pollinations":
        data, content_type = gen_pollinations(cfg_all, prompt.strip(), width, height, seed, model)
        out_ext = ext_from_content_type(content_type)
    elif backend == "siliconflow":
        data = gen_siliconflow(cfg_all, prompt.strip(), width, height, model)
    elif backend == "vertex":
        data = gen_vertex(cfg_all, prompt.strip(), width, height, model)
    elif backend == "sd-webui":
        bc = cfg_all.get("sd_webui", {})
        data = gen_sd_webui(
            cfg_all,
            prompt.strip(),
            negative.strip(),
            width,
            height,
            seed,
            int(steps if steps is not None else bc.get("steps", 28)),
            float(cfg if cfg is not None else bc.get("cfg_scale", 7)),
            str(bc.get("sampler_name", "Euler a")),
        )
    else:
        bc = cfg_all.get("comfyui", {})
        data = gen_comfyui(
            cfg_all,
            prompt.strip(),
            negative.strip(),
            width,
            height,
            seed,
            int(steps if steps is not None else bc.get("steps", 28)),
            float(cfg if cfg is not None else bc.get("cfg", 7)),
            str(bc.get("sampler_name", "euler")),
            str(bc.get("scheduler", "normal")),
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
    mirror = mirror_output(str(out_path), cfg_all)
    if mirror:
        result["mirror_path"] = mirror
    return result


# ---------------------------------------------------------------- commands

def cmd_generate(args: argparse.Namespace) -> dict[str, Any]:
    return generate_image(
        args.prompt,
        backend=args.backend,
        out=args.out,
        size=args.size,
        seed=args.seed,
        negative=args.negative,
        steps=args.steps,
        cfg=args.cfg,
        model=args.model,
    )


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
        "--backend", default="auto", help="pollinations / siliconflow / sd-webui / comfyui"
    )
    gen.add_argument("--out", help="输出文件路径")
    gen.add_argument("--size", default="1024x1024", help="分辨率，如 1024x1024")
    gen.add_argument("--seed", type=int, default=None, help="随机种子")
    gen.add_argument("--negative", default="", help="负面提示词（sd-webui / comfyui）")
    gen.add_argument("--steps", type=int, default=None, help="采样步数（sd-webui / comfyui）")
    gen.add_argument("--cfg", type=float, default=None, help="引导强度（sd-webui / comfyui）")
    gen.add_argument("--model", default="", help="模型（pollinations / siliconflow）")
    gen.add_argument("--json", action="store_true", help="输出 JSON（机器可读）")

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
