"""配置读取/保存/密钥掩码/角色表。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


APP_NAME = "deepseek-imagegen"
CONFIG_DIR = Path.home() / ".deepseek-imagegen"
CONFIG_FILE = CONFIG_DIR / "config.json"
VERTEX_DEFAULT_DIR = r"C:\Users\yjq\Documents\Codex\2026-07-31\new-chat\outputs\vertex-proxy\dist"
DEFAULT_MIRROR_DIR = r"C:\Users\yjq\Pictures\codex"


DEFAULT_CONFIG: dict[str, Any] = {
    "save_dir": "",
    "mirror_dir": DEFAULT_MIRROR_DIR,
    "default_size": "1024x1024",
    "translator": {
        "enabled": True,
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
    "characters": {
        "洛天依": {
            "aliases": ["洛天依-V4公式服"],
            "text": "银灰色中短发搭配两侧长双马尾；头顶银灰色八字环发髻，中央系天蓝色蝴蝶结缎带；戴天蓝色科技感圆盘耳机（带紫蓝与银白金属圈环）；亮绿色瞳孔；白色高领无袖露肩上衣，浅蓝与黑色包边，前襟旗袍式饰条；胸前挂明黄色短领带；腰间挂红色中国结（带流苏）；天蓝与白色相间短百褶裙；黑色长袖套，手腕戴浅蓝色珠状手环；黑色长筒袜（左右不对称、袜口浅蓝色菱形花纹）；异色短靴（右脚白底蓝尖、左脚天蓝）。整体为日系少女风。禁忌：不得改成Q版/大头比例；不得改变发色瞳色；不得漏掉双马尾、八字环发髻、耳机、黄色领带、中国结等标志性元素；不得写错服装配色。",
            "image": "",
        }
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
            from .http import GenError

            raise GenError(f"配置文件解析失败（{CONFIG_FILE}）：{exc}") from exc
    return cfg


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
