"""ConfigService：统一配置读取 / 保存 / 打码 / 路径 / 迁移。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import CONFIG_FILE, load_config, mask_config, save_config


class ConfigService:
    """外围客户端唯一配置入口（WebUI 不再直接读写配置文件）。"""

    def load(self) -> dict[str, Any]:
        """合并默认值后的生效配置（含旧配置迁移与环境变量）。"""
        return load_config()

    def load_raw(self) -> dict[str, Any]:
        """读取用户配置文件原文（不合并默认值）；不存在或损坏时返回空 dict。"""
        if not CONFIG_FILE.exists():
            return {}
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def save(self, cfg: dict[str, Any]) -> str:
        """原子写入用户配置文件，返回路径。"""
        return save_config(cfg)

    def masked(self) -> dict[str, Any]:
        """返回密钥已打码的生效配置。"""
        return mask_config(self.load())

    def path(self) -> Path:
        return CONFIG_FILE

    def exists(self) -> bool:
        return CONFIG_FILE.exists()
