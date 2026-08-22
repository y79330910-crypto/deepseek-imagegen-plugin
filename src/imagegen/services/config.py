"""ConfigService：统一配置读取 / 保存 / 打码 / 路径 / 迁移。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from ..config import default_config_path, load_config, mask_config, save_config


SECRET_KEYS = {"api_key", "password", "token", "key"}


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "on", "1")


def _to_int(value: Any) -> int:
    return int(str(value).strip())


def _to_float(value: Any) -> float:
    return float(str(value).strip())


def _to_list(value: Any) -> list[str]:
    return [s.strip() for s in str(value).split(",") if s.strip()]


def _normalize_patch(patch: Mapping[str, Any]) -> dict[str, Any]:
    """按已知配置字段做类型规范化（Web 表单字符串 → 配置类型）。

    只转换明确已知的字段，不做全局“智能猜类型”，避免把 API Key 等误转成数字。
    未知字段透传（保持既有行为）。
    """
    result: dict[str, Any] = json.loads(json.dumps(dict(patch)))
    pl = result.get("prompt_library")
    if isinstance(pl, dict):
        if isinstance(pl.get("categories"), str):
            pl["categories"] = _to_list(pl["categories"])
        for key in ("top_k", "final_k", "priority_count"):
            if key in pl and pl[key] not in ("", None):
                pl[key] = _to_int(pl[key])
        for key in ("enabled", "use_in_translator"):
            if key in pl and isinstance(pl[key], str):
                pl[key] = _to_bool(pl[key])
        rr = pl.get("rerank")
        if isinstance(rr, dict) and isinstance(rr.get("enabled"), str):
            rr["enabled"] = _to_bool(rr["enabled"])
        mysql = pl.get("mysql")
        if isinstance(mysql, dict) and mysql.get("port") not in ("", None):
            mysql["port"] = _to_int(mysql["port"])
    ref = result.get("reference")
    if isinstance(ref, dict) and isinstance(ref.get("auto_classify"), str):
        ref["auto_classify"] = _to_bool(ref["auto_classify"])
    sc = result.get("size_check")
    if isinstance(sc, dict) and sc.get("tolerance") not in ("", None):
        sc["tolerance"] = _to_float(sc["tolerance"])
    for node_key in ("translator", "size_check"):
        node = result.get(node_key)
        if isinstance(node, dict) and isinstance(node.get("enabled"), str):
            node["enabled"] = _to_bool(node["enabled"])
    return result


def _deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """递归合并 patch；None 值跳过（表示不修改）。"""
    result = dict(base)
    for key, value in override.items():
        if value is None:
            continue
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _protect_masked_secrets(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """patch 值等于当前 masked 表示时保留原 secret，防止 WebUI 回传打码值覆盖真实值。"""
    masked = mask_config(base)

    def walk(patch_node: Any, base_node: Any, masked_node: Any) -> Any:
        if not isinstance(patch_node, dict):
            return patch_node
        result = dict(patch_node)
        for key, value in list(result.items()):
            if isinstance(value, dict):
                result[key] = walk(
                    value,
                    base_node.get(key, {}) if isinstance(base_node, dict) else {},
                    masked_node.get(key, {}) if isinstance(masked_node, dict) else {},
                )
            elif key in SECRET_KEYS and isinstance(value, str):
                masked_value = masked_node.get(key) if isinstance(masked_node, dict) else None
                if value == masked_value:
                    base_value = base_node.get(key) if isinstance(base_node, dict) else None
                    result[key] = base_value if base_value is not None else ""
        return result

    return walk(patch, base, masked)


class ConfigService:
    """外围客户端唯一配置入口（WebUI 不再直接读写配置文件）。

    path 支持 str / Path / None；None 时使用 default_config_path()。
    所有方法都基于实例路径 self.config_path，不会回落模块级常量。
    """

    def __init__(self, path: str | Path | None = None):
        self.config_path = (
            Path(path).expanduser() if path is not None else default_config_path()
        )

    def load(self) -> dict[str, Any]:
        """合并默认值后的生效配置（含旧配置迁移与环境变量）。"""
        return load_config(self.config_path)

    def load_raw(self) -> dict[str, Any]:
        """读取用户配置文件原文（不合并默认值）；不存在或损坏时返回空 dict。"""
        if not self.config_path.exists():
            return {}
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def save(self, cfg: dict[str, Any]) -> str:
        """原子写入用户配置文件，返回路径。"""
        return save_config(cfg, self.config_path)

    def masked(self) -> dict[str, Any]:
        """返回密钥已打码的生效配置。"""
        return mask_config(self.load())

    def path(self) -> Path:
        return self.config_path

    def exists(self) -> bool:
        return self.config_path.exists()

    def update(self, patch: Mapping[str, Any]) -> dict[str, Any]:
        """统一配置更新语义：规范化 → 深度合并 → 保护 masked secret → 保存 → 返回打码配置。

        WebUI / CLI / 未来 HTTP 都通过此方法修改配置，不再各自实现合并逻辑。
        """
        base = self.load_raw()
        if not isinstance(base, dict):
            base = {}
        # masked 比较基于“迁移后生效配置”，保证旧式密钥字段也能被正确保护
        effective = self.load()
        normalized = _normalize_patch(patch)
        protected = _protect_masked_secrets(effective, normalized)
        merged = _deep_merge(base, protected)
        self.save(merged)
        return self.masked()
