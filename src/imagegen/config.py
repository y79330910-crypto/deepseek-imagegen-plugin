"""配置读取/保存/密钥掩码（独立于任何宿主环境）。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


APP_NAME = "deepseek-imagegen"
CONFIG_DIR = Path.home() / ".deepseek-imagegen"
CONFIG_FILE = CONFIG_DIR / "config.json"
# 独立程序不内置任何宿主专属路径：
# - vertex.dir 缺省时可使用环境变量 VERTEX_PROXY_DIR 指定
# - mirror_dir 缺省时可使用环境变量 IMAGEGEN_MIRROR_DIR 指定
VERTEX_DEFAULT_DIR = ""
MIRROR_DIR_ENV = "IMAGEGEN_MIRROR_DIR"
DEFAULT_MIRROR_DIR = ""


DEFAULT_CONFIG: dict[str, Any] = {
    "save_dir": "",
    "mirror_dir": DEFAULT_MIRROR_DIR,
    "default_size": "1024x1024",
    "translator": {
        "engine": "deepseek",
        "output_lang": "zh",
        "deepseek": {
            "base_url": "",
            "api_key": "",
            "model": "deepseek-v4-flash",
        },
        "gemini": {
            "model": "",
        },
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
    "size_policy": {
        "mode": "auto",
        "retries": 2,
        "tolerance": 0.06,
        "probe_cache": {},
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
    "vertex": {
        "dir": VERTEX_DEFAULT_DIR,
        "base_url": "",
        "api_key": "",
        "model": "",
    },
    "extra_backends": {},
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


def _migrate_translator(cfg: dict[str, Any]) -> dict[str, Any]:
    """统一 translator 开关：Core 内部只保留 engine 一种最终状态。

    旧配置同时有 enabled + engine 时：enabled=false → engine=off；
    否则保留 engine（缺省 deepseek）。迁移后不再维护 enabled。
    """
    tr = cfg.get("translator")
    if isinstance(tr, dict) and "enabled" in tr:
        if not bool(tr.get("enabled", True)):
            tr["engine"] = "off"
        elif not str(tr.get("engine") or "").strip():
            tr["engine"] = "deepseek"
        tr.pop("enabled", None)
    return cfg


def load_config() -> dict[str, Any]:
    """读取用户配置并合并默认值。"""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if CONFIG_FILE.exists():
        try:
            with CONFIG_FILE.open("r", encoding="utf-8") as handle:
                user_cfg = json.load(handle)
            if isinstance(user_cfg, dict):
                cfg = deep_merge(cfg, user_cfg)
        except (OSError, json.JSONDecodeError) as exc:
            from .errors import GenError

            raise GenError(f"配置文件解析失败（{CONFIG_FILE}）：{exc}") from exc
    if not str(cfg.get("mirror_dir") or "").strip():
        env_mirror = os.environ.get(MIRROR_DIR_ENV, "").strip()
        if env_mirror:
            cfg["mirror_dir"] = env_mirror
    return _migrate_translator(cfg)


def save_config(cfg: dict[str, Any]) -> str:
    """原子写入配置（doctor 尺寸探针缓存用）。"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = str(CONFIG_FILE) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(cfg, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_FILE)
    return str(CONFIG_FILE)


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
