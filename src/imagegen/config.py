"""配置读取/保存/密钥掩码（独立于任何宿主环境）。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


APP_NAME = "deepseek-imagegen"
# 独立程序不内置任何宿主专属路径：
# - mirror_dir 缺省时可使用环境变量 IMAGEGEN_MIRROR_DIR 指定
MIRROR_DIR_ENV = "IMAGEGEN_MIRROR_DIR"
DEFAULT_MIRROR_DIR = ""


def default_config_path() -> Path:
    """ImageGen 默认用户配置路径（单一来源，跨模块复用）。"""
    return Path.home() / ".deepseek-imagegen" / "config.json"


def default_history_db_path() -> Path:
    """ImageGen 默认历史数据库路径（单一来源，跨模块复用）。"""
    return Path.home() / ".deepseek-imagegen" / "imagegen.db"


def default_asset_dir() -> Path:
    """ImageGen 默认 managed asset 目录（单一来源，跨模块复用）。"""
    return Path.home() / ".deepseek-imagegen" / "assets" / "references"


# 兼容常量（新代码优先使用 default_config_path()）
CONFIG_DIR = Path.home() / ".deepseek-imagegen"
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


def _migrate_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """旧配置迁移到 Phase 6 双 OpenAI-Compatible 结构（简单、确定、可预测）。

    - translator.deepseek.{base_url,api_key,model} → translator.{...}（值明确才迁移）
    - translator.engine off/none/direct/直传 → translator.enabled=false
    - vertex / extra_backends.* 不猜测哪个应成为 image 上游 → image.* 保持为空
    - size_policy.tolerance → size_check.tolerance
    """
    tr = cfg.get("translator")
    if isinstance(tr, dict):
        if "engine" in tr:
            legacy_engine = str(tr.get("engine") or "deepseek").strip().lower()
            tr["enabled"] = legacy_engine not in ("off", "none", "direct", "直传")
            tr.pop("engine", None)
        old = tr.get("deepseek")
        if isinstance(old, dict):
            for key in ("base_url", "api_key", "model"):
                value = str(old.get(key) or "").strip()
                if value and not str(tr.get(key) or "").strip():
                    tr[key] = value
        tr.pop("deepseek", None)
        tr.pop("gemini", None)
    if not isinstance(cfg.get("image"), dict):
        cfg["image"] = {}
    sc = cfg.pop("size_policy", None)
    if isinstance(sc, dict) and "tolerance" in sc:
        cfg.setdefault("size_check", {})
        if isinstance(cfg.get("size_check"), dict):
            try:
                cfg["size_check"]["tolerance"] = float(sc["tolerance"])
            except (TypeError, ValueError):
                pass
    cfg.pop("vertex", None)
    cfg.pop("extra_backends", None)
    return cfg


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    """读取用户配置并合并默认值；config_path 缺省时使用默认路径。"""
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
            from .errors import ConfigurationError

            raise ConfigurationError(f"配置文件解析失败（{cfg_path}）：{exc}") from exc
    if not str(cfg.get("mirror_dir") or "").strip():
        env_mirror = os.environ.get(MIRROR_DIR_ENV, "").strip()
        if env_mirror:
            cfg["mirror_dir"] = env_mirror
    # 提示词上游兼容：Codex 宿主注入的通用环境变量作为 translator 兜底
    tr = cfg.get("translator")
    if isinstance(tr, dict):
        if not str(tr.get("base_url") or "").strip():
            tr["base_url"] = os.environ.get("DEEPSEEK_BASE_URL", "").strip()
        if not str(tr.get("api_key") or "").strip():
            tr["api_key"] = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    return _migrate_config(cfg)


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
