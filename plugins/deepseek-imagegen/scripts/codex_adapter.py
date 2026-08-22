"""Codex 适配层：把 Codex 环境中的默认值注入 Core（Core 本身不依赖 Codex）。

这里集中存放所有“只有 Codex 宿主才存在”的发现逻辑：
旧版默认代理目录 / 镜像目录、~/.codex/config.toml 里的 DeepSeek 凭据、
deepseek-vision 桥接脚本位置。Core 只读取通用环境变量与用户配置。
"""

from __future__ import annotations

import os
import re
from pathlib import Path


CODEX_CONFIG_TOML = Path.home() / ".codex" / "config.toml"
LEGACY_MIRROR_DIR = r"C:\Users\yjq\Pictures\codex"
LEGACY_VISION_ROOT = Path.home() / ".codex" / "plugins" / "cache" / "deepseek-vision"


def _load_deepseek_credentials() -> tuple[str, str]:
    """从环境变量或 ~/.codex/config.toml 读取 DeepSeek 地址与密钥（不回显密钥）。"""
    base_url = os.environ.get("DEEPSEEK_BASE_URL", "").strip()
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if CODEX_CONFIG_TOML.is_file():
        try:
            text = CODEX_CONFIG_TOML.read_text(encoding="utf-8")
        except OSError:
            return base_url, api_key
        match = re.search(
            r"\[model_providers\.deepseek\][^\[]*?base_url\s*=\s*\"([^\"]+)\"[^\[]*?"
            r"experimental_bearer_token\s*=\s*\"([^\"]+)\"",
            text,
            re.S,
        )
        if match:
            if not base_url:
                base_url = match.group(1).strip().rstrip("/")
            if not api_key:
                api_key = match.group(2).strip()
    return base_url, api_key


def prepare_environment() -> None:
    """把 Codex 宿主默认值注入通用环境变量；可重复调用，不覆盖用户已设置的值。"""
    os.environ.setdefault("IMAGEGEN_MIRROR_DIR", LEGACY_MIRROR_DIR)
    base_url, api_key = _load_deepseek_credentials()
    if base_url:
        os.environ.setdefault("DEEPSEEK_BASE_URL", base_url)
    if api_key:
        os.environ.setdefault("DEEPSEEK_API_KEY", api_key)
    if LEGACY_VISION_ROOT.is_dir():
        hits = sorted(LEGACY_VISION_ROOT.rglob("vision_bridge.py"))
        if hits:
            os.environ.setdefault("IMAGEGEN_VISION_SCRIPT", str(hits[0]))
