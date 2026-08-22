"""配置读取/保存/密钥掩码（独立于任何宿主环境）。"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

from .errors import ConfigurationError


APP_NAME = "imagegen"
DEFAULT_MIRROR_DIR = ""

# ---------------------------------------------------------------------------
# IMAGEGEN_* 环境变量（固定优先级：DEFAULT_CONFIG < config.json < IMAGEGEN_*）
# 字符串：环境变量只要存在就视为显式 override，允许空字符串。
# 布尔：  true/false、1/0、on/off、yes/no（大小写不敏感），非法值抛
#         ConfigurationError，不得静默转成 False。
# 浮点：  显式转换并校验（必须是正数），非法值抛 ConfigurationError。
# ---------------------------------------------------------------------------

_STRING_ENV_KEYS: dict[str, tuple[str, ...]] = {
    "IMAGEGEN_TRANSLATOR_BASE_URL": ("translator", "base_url"),
    "IMAGEGEN_TRANSLATOR_API_KEY": ("translator", "api_key"),
    "IMAGEGEN_TRANSLATOR_MODEL": ("translator", "model"),
    "IMAGEGEN_TRANSLATOR_OUTPUT_LANG": ("translator", "output_lang"),
    "IMAGEGEN_IMAGE_BASE_URL": ("image", "base_url"),
    "IMAGEGEN_IMAGE_API_KEY": ("image", "api_key"),
    "IMAGEGEN_IMAGE_MODEL": ("image", "model"),
    "IMAGEGEN_IMAGE_QUALITY": ("image", "quality"),
    "IMAGEGEN_DEFAULT_SIZE": ("default_size",),
    "IMAGEGEN_SAVE_DIR": ("save_dir",),
    "IMAGEGEN_MIRROR_DIR": ("mirror_dir",),
}

_BOOL_ENV_KEYS: dict[str, tuple[str, ...]] = {
    "IMAGEGEN_SIZE_CHECK_ENABLED": ("size_check", "enabled"),
}

_FLOAT_ENV_KEYS: dict[str, tuple[str, ...]] = {
    "IMAGEGEN_SIZE_CHECK_TOLERANCE": ("size_check", "tolerance"),
}

_BOOL_TRUE = {"true", "1", "on", "yes"}
_BOOL_FALSE = {"false", "0", "off", "no"}


def _set_path(cfg: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    node = cfg
    for key in path[:-1]:
        if not isinstance(node.get(key), dict):
            node[key] = {}
        node = node[key]
    node[path[-1]] = value


def _parse_bool_env(name: str, value: str) -> bool:
    raw = (value or "").strip().lower()
    if raw in _BOOL_TRUE:
        return True
    if raw in _BOOL_FALSE:
        return False
    raise ConfigurationError(
        f"环境变量 {name} 取值无效：{value!r}；"
        "必须为 true/false、1/0、on/off、yes/no（大小写不敏感）。"
    )


def _parse_float_env(name: str, value: str) -> float:
    raw = (value or "").strip()
    try:
        result = float(raw)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(
            f"环境变量 {name} 必须是数字，当前值：{value!r}。"
        ) from exc
    if not math.isfinite(result) or result <= 0:
        raise ConfigurationError(
            f"环境变量 {name} 必须是正数，当前值：{value!r}。"
        )
    return result


def _apply_env_overrides(cfg: dict[str, Any]) -> None:
    """把 IMAGEGEN_* 环境变量应用到生效配置（env > config.json > default）。"""
    for name, path in _STRING_ENV_KEYS.items():
        if name in os.environ:
            _set_path(cfg, path, os.environ[name])
    for name, path in _BOOL_ENV_KEYS.items():
        if name in os.environ:
            _set_path(cfg, path, _parse_bool_env(name, os.environ[name]))
    for name, path in _FLOAT_ENV_KEYS.items():
        if name in os.environ:
            _set_path(cfg, path, _parse_float_env(name, os.environ[name]))


def default_config_path() -> Path:
    """ImageGen 默认用户配置路径（单一来源，跨模块复用）。"""
    return Path.home() / ".imagegen" / "config.json"


def default_history_db_path() -> Path:
    """ImageGen 默认历史数据库路径（单一来源，跨模块复用）。"""
    return Path.home() / ".imagegen" / "imagegen.db"


def default_asset_dir() -> Path:
    """ImageGen 默认 managed asset 目录（单一来源，跨模块复用）。"""
    return Path.home() / ".imagegen" / "assets" / "references"


# 兼容常量（新代码优先使用 default_config_path()）
CONFIG_DIR = Path.home() / ".imagegen"
CONFIG_FILE = default_config_path()


DEFAULT_CONFIG: dict[str, Any] = {
    "save_dir": "",
    "mirror_dir": DEFAULT_MIRROR_DIR,
    "default_size": "1024x1024",
    "translator": {
        "enabled": True,
        "base_url": "",
        "api_key": "",
        "model": "",
        "output_lang": "zh",
    },
    "image": {
        "base_url": "",
        "api_key": "",
        "model": "",
        "quality": "",
    },
    "composition": {
        "preset": "auto",
        "presets": {
            "full-body": {
                "size": "768x1408",
                "prompt": "全身构图：人物从头到脚完整入画，双脚完整可见且位于画面底部，头顶留出适当空白，标准日系插画人体比例（非Q版、非大头娃娃）。",
                "checklist": ["双脚完整入画", "头顶留白", "全身从头到脚完整", "非Q版人体比例"],
            },
            "half-body": {
                "size": "1024x1024",
                "prompt": "半身构图：人物腰部以上完整入画，头顶留出适当空白，双手自然入画或部分入画，日系插画人体比例（非Q版）。",
                "checklist": ["腰部以上完整入画", "头顶留白", "非Q版人体比例"],
            },
            "portrait": {
                "size": "1024x1024",
                "prompt": "特写构图：人物面部与肩部清晰完整，面部居中偏上，头顶留白，五官比例自然，日系插画风格。",
                "checklist": ["面部完整清晰", "头顶留白", "构图以面部特写为主"],
            },
            "landscape": {
                "size": "1408x768",
                "prompt": "横版广角构图：主体完整入画，前景中景背景层次分明，画面左右留出环境空间，适合演唱会舞台场景。",
                "checklist": ["主体完整入画", "横向广角构图", "背景层次清晰"],
            },
        },
    },
    "size_check": {
        "enabled": True,
        "tolerance": 0.06,
    },
    "reference": {
        "auto_classify": True,
        "vision_script": "",
        "classify_timeout": 90,
    },
    "prompt_library": {
        "enabled": False,
        "use_in_translator": True,
        "top_k": 30,
        "final_k": 6,
        "categories": [],
        "priority_category": "自家精品",
        "priority_count": 3,
        "embedding": {
            "base_url": "https://api.siliconflow.com/v1/embeddings",
            "api_key": "",
            "model": "Qwen/Qwen3-Embedding-8B",
        },
        "rerank": {
            "enabled": True,
            "base_url": "https://api.siliconflow.com/v1/rerank",
            "api_key": "",
            "model": "Qwen/Qwen3-Reranker-8B",
        },
        "mysql": {
            "host": "127.0.0.1",
            "port": 3306,
            "user": "",
            "password": "",
            "db": "prompt_library",
        },
    },
}


def deep_merge(base: dict, override: dict) -> dict:
    """递归合并配置：用户配置覆盖默认值。"""
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    """读取生效配置：DEFAULT_CONFIG < config.json < IMAGEGEN_*。

    config_path 缺省时使用默认路径。只读取新路径，不识别 / 不迁移旧配置。
    """
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    cfg_path = (
        Path(config_path).expanduser()
        if config_path is not None
        else default_config_path()
    )
    if cfg_path.exists():
        try:
            with cfg_path.open("r", encoding="utf-8") as handle:
                user_cfg = json.load(handle)
            if isinstance(user_cfg, dict):
                cfg = deep_merge(cfg, user_cfg)
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigurationError(f"配置文件解析失败（{cfg_path}）：{exc}") from exc
    _apply_env_overrides(cfg)
    return cfg


def save_config(cfg: dict[str, Any], config_path: str | Path | None = None) -> str:
    """原子写入配置；config_path 缺省时使用默认路径。"""
    cfg_path = (
        Path(config_path).expanduser()
        if config_path is not None
        else default_config_path()
    )
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(cfg_path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(cfg, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, cfg_path)
    return str(cfg_path)


def mask_key(key: str) -> str:
    """密钥打码：短密钥全打星，长密钥只留头尾。"""
    if not key:
        return "(未设置)"
    if len(key) <= 8:
        return "*" * len(key)
    return key[:4] + "*" * (len(key) - 8) + key[-4:]


def mask_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """返回一份所有密钥/密码都已打码的配置副本。"""
    safe = json.loads(json.dumps(cfg))

    def mask_path(node: dict, path: str) -> None:
        for key, value in list(node.items()):
            if isinstance(value, dict):
                mask_path(value, f"{path}.{key}")
            elif key in ("api_key", "password", "token", "key") and isinstance(value, str):
                node[key] = mask_key(value)

    mask_path(safe, "")
    return safe
