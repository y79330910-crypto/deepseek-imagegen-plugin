"""构图预设：画幅 + 提示词硬约束 + 检查清单。"""

from __future__ import annotations

from typing import Any

from .errors import GenError


COMPOSITION_ALIASES = {
    "auto": "auto",
    "full": "full-body",
    "fullbody": "full-body",
    "full-body": "full-body",
    "全身": "full-body",
    "全身图": "full-body",
    "half": "half-body",
    "halfbody": "half-body",
    "half-body": "half-body",
    "半身": "half-body",
    "半身图": "half-body",
    "portrait": "portrait",
    "特写": "portrait",
    "头": "portrait",
    "landscape": "landscape",
    "横版": "landscape",
    "风景": "landscape",
}


def resolve_composition(name: str, cfg: dict[str, Any]) -> str:
    """解析构图预设参数（支持中英文别名），未指定时跟随配置。"""
    key = (name or "auto").strip().lower()
    if key in ("", "auto"):
        comp = cfg.get("composition") or {}
        if isinstance(comp, dict):
            key = str(comp.get("preset") or "auto").strip().lower()
    resolved = COMPOSITION_ALIASES.get(key, key)
    presets = (cfg.get("composition") or {}).get("presets") or {}
    if resolved != "auto" and resolved not in presets:
        raise GenError(
            f"未知构图预设：{name!r}。可选：full-body / half-body / portrait / landscape / auto"
        )
    return resolved


def get_composition_preset(name: str, cfg: dict[str, Any]) -> dict[str, Any]:
    """按预设名返回构图配置；auto 返回空。"""
    key = resolve_composition(name, cfg)
    if key == "auto":
        return {}
    presets = (cfg.get("composition") or {}).get("presets") or {}
    return presets.get(key) or {}


def composition_prompt_suffix(name: str, cfg: dict[str, Any]) -> str:
    """返回要追加到提示词里的构图硬约束。"""
    preset = get_composition_preset(name, cfg)
    return str(preset.get("prompt") or "").strip()


def composition_checklist(name: str, cfg: dict[str, Any]) -> list[str]:
    """返回构图预设检查清单。"""
    preset = get_composition_preset(name, cfg)
    return [str(x).strip() for x in preset.get("checklist") or [] if str(x).strip()]
