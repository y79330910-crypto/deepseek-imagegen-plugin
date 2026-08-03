"""轻量角色表：精确文字匹配触发、别名、手动指定、参考图降级。"""

from __future__ import annotations

import re
from typing import Any, Optional

from .http import GenError


def iter_names(cfg: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """返回 [(角色键, 可匹配名称, 条目)]，名称包含角色键与别名。"""
    chars = cfg.get("characters") or {}
    if not isinstance(chars, dict):
        return []
    out: list[tuple[str, str, dict[str, Any]]] = []
    for key, entry in chars.items():
        if not isinstance(entry, dict):
            entry = {"text": str(entry)}
        names = [str(key)]
        for alias in entry.get("aliases") or []:
            if str(alias).strip():
                names.append(str(alias).strip())
        for name in names:
            if name:
                out.append((str(key), name, entry))
    return out


def find_character_by_name(cfg: dict[str, Any], name: str) -> Optional[tuple[str, dict[str, Any]]]:
    """按角色名/别名精确查找（--character 手动兜底）。"""
    name = (name or "").strip()
    if not name:
        return None
    for key, match_name, entry in iter_names(cfg):
        if match_name == name:
            return key, entry
    return None


def detect_character(cfg: dict[str, Any], prompt: str) -> Optional[str]:
    """精确文字匹配触发：提示词中出现角色名/别名即返回角色键。

    规则：角色名后紧跟“风格/风”不算点名（如“洛天依风格原创”不注入）；
    画其他人物/物品/风景零注入。
    """
    prompt = prompt or ""
    for key, match_name, _entry in iter_names(cfg):
        for match in re.finditer(re.escape(match_name), prompt):
            after = prompt[match.end() : match.end() + 2]
            if after.startswith("风格") or after.startswith("风"):
                continue
            return key
    return None


def character_desc(key: str, entry: dict[str, Any]) -> str:
    """生成角色设定注入文字（设定 + 禁忌原文）。"""
    text = str(entry.get("text") or "").strip()
    if not text:
        return ""
    return f"{key}：{text}"


def resolve_character(
    cfg: dict[str, Any],
    prompt: str,
    manual: str = "",
    character_image: str = "",
) -> dict[str, Any]:
    """统一角色解析：手动 > 自动精确匹配。

    返回：{used, key, desc, image, warning}
    image 为空表示没有参考图；warning 用于不中断出图的中文提示。
    """
    manual = (manual or "").strip()
    result: dict[str, Any] = {"used": False, "key": "", "desc": "", "image": "", "warning": ""}
    if manual:
        found = find_character_by_name(cfg, manual)
        if not found:
            result["warning"] = (
                f"角色表里没有“{manual}”，已跳过角色设定注入，继续正常出图。"
                "可先在 config.json 的 characters 段添加该角色。"
            )
            return result
        key, entry = found
    else:
        key = detect_character(cfg, prompt)
        if not key:
            return result
        entry = (cfg.get("characters") or {}).get(key) or {}
        if not isinstance(entry, dict):
            entry = {"text": str(entry)}
    desc = character_desc(key, entry)
    image = str(character_image or "").strip() or str(entry.get("image") or "").strip()
    result.update(
        {
            "used": True,
            "key": key,
            "desc": desc,
            "image": image,
            "warning": "",
        }
    )
    return result


def load_character_reference(image: str, target_w: int, target_h: int) -> tuple[bytes, str, str]:
    """读取角色参考图并按目标画幅等比适配（不拉伸）；失败抛 GenError 由调用方降级。"""
    from .image_utils import fit_reference_to_canvas, load_init_image

    data, mime, name = load_init_image(image)
    try:
        data, mime, name = fit_reference_to_canvas(data, mime, target_w, target_h)
    except GenError:
        # Pillow 不可用时降级：原图直发，由模型自行处理
        pass
    return data, mime, name
