# -*- coding: utf-8 -*-
"""参考图三段式提示词：类型模板、自动分类与避免项提取。"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from .errors import ValidationError


REF_TYPE_CHOICES = [
    "auto",
    "character",
    "outfit",
    "style",
    "scene",
    "composition",
    "pose",
    "object",
]

REF_TYPE_LABELS = {
    "character": "角色人物",
    "outfit": "服装造型",
    "style": "艺术风格",
    "scene": "场景背景",
    "composition": "构图布局",
    "pose": "姿势动作",
    "object": "物品产品",
    "generic": "通用主体",
}

# 每类参考图的三段式模板：第1段保持（锁死）→ 第2段改变 → 第3段场景
REFERENCE_TEMPLATES: dict[str, dict[str, str]] = {
    "character": {
        "keep": "参考图只提供角色的身份与外观：逐项保持身份锚点清单中的每一项"
                "（面容、发型发色、瞳色、身材比例、服装与标志性配饰）；"
                "未列入清单的旧图元素一律不保留。",
        "discard": "不保留参考图中的姿势、面部朝向、站立角度、构图、背景与棚拍光；"
                   "这些全部全新创作。",
        "change": "参考图只提供身份与外观：姿势、表情、角度、构图全部全新创作；"
                  "用户要求移除的元素从身份锚点清单中划除，其余身份锚点仍须逐项保留；"
                  "多个改变按重要性排序，过多时建议分两次生成。",
        "scene": "场景全新创作：按用户要求生成新场景与光线天气；"
                 "不得沿用参考图的背景、棚拍光或构图。",
    },
    "outfit": {
        "keep": "保持参考图中服装的款式、剪裁、配色、面料质感与全部装饰细节完全不变。",
        "change": "只执行用户对服装的指定调整（改色、改长短、增减配饰、换人穿着等）。",
        "scene": "把该服装穿到用户指定的主体上并放入用户指定场景；若未指定，则用干净简洁的展示背景。",
    },
    "style": {
        "keep": "保持参考图的画风、媒介、笔触、线条、上色方式、色调与光影质感完全不变。",
        "change": "只执行用户对画面内容的指定改变，风格语言始终沿用参考图。",
        "scene": "按用户要求重绘新主体与新场景，全部使用参考图的风格表现。",
    },
    "scene": {
        "keep": "保持参考图中的地点结构、建筑、植被、地标等环境元素不变；光线与天气可按用户要求调整。",
        "change": "只执行用户对环境的指定调整（季节、天气、光线、增删元素等）。",
        "scene": "把用户指定的主体放入该场景，并按用户要求的时间与光线重新打光，使主体自然融入。",
    },
    "composition": {
        "keep": "保持参考图的景别、机位角度、主体位置、画面留白与宽高比完全不变。",
        "change": "只替换画面内的主体、内容或背景。",
        "scene": "在完全相同的构图框架内重绘用户指定的新内容与新场景。",
    },
    "pose": {
        "keep": "保持参考图中人物的身体姿势、手部动作、表情与镜头角度完全不变。",
        "change": "只修改服装、场景、风格等其余部分，姿势本身不动。",
        "scene": "按用户要求生成新环境与背景，沿用同一姿势。",
    },
    "object": {
        "keep": "保持参考图中物品的形态、材质、配色、logo 与关键细节完全不变。",
        "change": "只执行用户对物品的指定调整（去装饰点、换角度、改摆放等）。",
        "scene": "把该物品放入用户指定场景并按用户要求打光；若未指定，则用干净简洁的展示背景。",
    },
    "generic": {
        "keep": "参考图整体作为主体基准：保持图中可见的主体特征与关键细节不变。",
        "change": "只执行用户指定的改变，未提及的部分一律保持原样。",
        "scene": "按用户要求生成新场景；若未指定，则沿用参考图中的环境。",
    },
}


def validate_ref_type(name: str) -> str:
    """校验参考图类型参数；未知类型直接报错。"""
    key = (name or "auto").strip().lower()
    if key not in REF_TYPE_CHOICES:
        raise ValidationError(
            f"未知参考图类型：{name!r}。可选：{', '.join(REF_TYPE_CHOICES)}"
        )
    return key


_AVOID_RE = re.compile(
    r"(?:不要出现|不要|别画|别|不画|不出现|去除|去掉|移除|删掉|删去|擦掉)"
    r"\s*([^，。；、\n]{1,14})"
)
_AVOID_SUFFIX_RE = re.compile(r"([^，。；、\n]{1,12})(?:去掉|去除|移除|删掉|删去)")


def _clean_avoid_item(item: str) -> str:
    item = item.strip().lstrip("的")
    item = re.sub(r"[的了嘛吧呢些](?=$)", "", item)
    return item.strip()


def detect_avoid_items(text: str) -> list[str]:
    """从用户需求里提取“不要/去除/去掉 X”类禁止项，用于改变段与硬约束后缀。"""
    if not text:
        return []
    items: list[str] = []
    for match in _AVOID_RE.finditer(text):
        item = _clean_avoid_item(match.group(1))
        if not item:
            continue
        if item not in items:
            items.append(item)
        if len(items) >= 5:
            break
    for match in _AVOID_SUFFIX_RE.finditer(text):
        item = _clean_avoid_item(match.group(1))
        if not item or item in items:
            continue
        items.append(item)
        if len(items) >= 5:
            break
    return _dedupe_avoid(items)


def _dedupe_avoid(items: list[str]) -> list[str]:
    """去掉包含关系冗余：同时出现“耳机”和“任何耳机”时只保留“耳机”。"""
    return [
        item for item in items
        if not any(other != item and other in item for other in items)
    ]


def build_reference_brief(
    ref_type: str,
    user_text: str = "",
    preserve: str = "",
    avoid: Optional[list[str]] = None,
    identity_list: str = "",
) -> str:
    """生成参考图简报：类型 + 三段式规则 + 关键保持点 + 禁止项。"""
    tpl = REFERENCE_TEMPLATES.get(ref_type) or REFERENCE_TEMPLATES["generic"]
    label = REF_TYPE_LABELS.get(ref_type, REF_TYPE_LABELS["generic"])
    avoid = avoid if avoid is not None else detect_avoid_items(user_text)
    lines = ["【参考图简报】", f"类型：{label}"]
    keep = tpl["keep"]
    if identity_list:
        keep += "\n身份锚点清单（必须逐项保留）：" + identity_list
    lines.append("第1段·保持：" + keep)
    if tpl.get("discard"):
        lines.append("不保留（场景锚点，全新创作）：" + tpl["discard"])
    lines.append("第2段·改变：" + tpl["change"])
    lines.append("第3段·场景：" + tpl["scene"])
    if preserve:
        lines.append("参考图关键特征（供保持段参考）：" + preserve)
    if avoid:
        lines.append("用户划除的锚点/禁止项（不作为保留项）：" + "、".join(avoid))
    return "\n".join(lines)


def build_reference_suffix(
    ref_type: str,
    avoid: Optional[list[str]] = None,
    identity_list: str = "",
) -> str:
    """生成最终提示词的参考图硬约束后缀，防止翻译官漏掉约束。"""
    tpl = REFERENCE_TEMPLATES.get(ref_type) or REFERENCE_TEMPLATES["generic"]
    lines = ["（参考图硬性要求，必须遵守）"]
    keep = tpl["keep"]
    if identity_list:
        keep += "\n身份锚点清单（必须逐项保留）：" + identity_list
    lines.append("第1段·保持：" + keep)
    if tpl.get("discard"):
        lines.append("不保留（场景锚点，全新创作）：" + tpl["discard"])
    lines.append("第2段·改变：" + tpl["change"])
    lines.append("第3段·场景：" + tpl["scene"])
    if avoid:
        lines.append(
            "用户划除（不作为保留项）：" + "、".join(avoid)
            + "；其余身份锚点仍须保留，画面中不要出现上述元素。"
        )
    return "\n".join(lines)


# ---------- 多参考图（多图分工） ----------

MAX_REF_IMAGES = 4
AUTO_ROLE_ORDER = ["outfit", "pose", "style", "scene", "object"]

ROLE_QUESTION_TMPL = (
    "这张图片作为生图参考图的「{label}」用途。请只回答 JSON，不要其他文字："
    '{{"preserve":"一句话列出这张图里最需要保持的{label}关键特征"}}'
)


def _parse_preserve_result(raw: str) -> str:
    """从视觉桥接结果里提取 preserve 一句话（不要求 type 字段）。"""
    if not raw:
        return ""
    block = re.search(r"\{.*\}", raw, re.S)
    candidates = [raw.strip()]
    if block:
        candidates.append(block.group(0))
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict):
            return str(data.get("preserve") or "").strip()
    return ""


def extract_reference_features(
    image: str,
    explicit_role: str = "",
    cfg: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """按用途提取参考图关键特征：显式用途走用途提问，auto 走自动分类。

    返回 {ok, type, preserve, method, error}；识别失败时降级（preserve 为空、type 保留显式用途）。
    """
    cfg = cfg if cfg is not None else {}
    role = (explicit_role or "").strip().lower()
    if role and role != "auto":
        try:
            role = validate_ref_type(role)
        except ValidationError:
            role = "auto"
    ref_cfg = cfg.get("reference") or {}
    if not isinstance(ref_cfg, dict):
        ref_cfg = {}
    if not bool(ref_cfg.get("auto_classify", True)):
        method = "manual" if role and role != "auto" else "disabled"
        return {"ok": False, "type": role, "preserve": "", "method": method,
                "error": "auto_classify=false，已跳过特征提取"}
    script = find_vision_bridge(cfg)
    if not script:
        method = "manual" if role and role != "auto" else "fallback"
        return {"ok": False, "type": role, "preserve": "", "method": method,
                "error": "vision_bridge 未找到"}
    timeout = int(ref_cfg.get("classify_timeout") or 90)
    if role and role != "auto":
        label = REF_TYPE_LABELS.get(role, role)
        question = ROLE_QUESTION_TMPL.format(label=label)
        try:
            proc = subprocess.run(
                [sys.executable, script, "ask", image, question, "--json"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "type": role, "preserve": "", "method": "fallback",
                    "error": f"vision_bridge 调用失败：{exc}"[:200]}
        if proc.returncode != 0:
            return {"ok": False, "type": role, "preserve": "", "method": "fallback",
                    "error": proc.stderr.strip()[:200]}
        try:
            data = json.loads(proc.stdout)
            raw = str((data or {}).get("result") or "").strip()
        except (json.JSONDecodeError, TypeError):
            raw = proc.stdout.strip()
        preserve = _parse_preserve_result(raw)
        return {"ok": bool(preserve), "type": role, "preserve": preserve,
                "method": "manual", "error": "" if preserve else "用途特征识别结果无法解析"}
    return classify_reference(image, cfg)


def build_multi_reference_brief(
    items: list[dict[str, Any]],
    avoid: Optional[list[str]] = None,
) -> str:
    """多图分工简报：每张图一段 + 按用途的隔离硬规则 + 禁止项。"""
    avoid = avoid or []
    lines = ["【参考图简报】", f"参考图数量：{len(items)}，多图分工如下："]
    for idx, it in enumerate(items, 1):
        role = str(it.get("type") or "generic")
        tpl = REFERENCE_TEMPLATES.get(role) or REFERENCE_TEMPLATES["generic"]
        label = REF_TYPE_LABELS.get(role, REF_TYPE_LABELS["generic"])
        preserve = str(it.get("preserve") or "").strip()
        keep = tpl["keep"]
        if preserve:
            keep += "\n关键特征（必须逐项保留）：" + preserve
        lines.append(f"图{idx}（{label}）：{keep}")
    lines.append("多图分工硬性规则（必须遵守）：")
    lines.append("- 每张图只提供自己声明的用途特征，图与图之间互不借用；")
    by_role: dict[str, list[int]] = {}
    for idx, it in enumerate(items, 1):
        by_role.setdefault(str(it.get("type") or "generic"), []).append(idx)
    for role, idxs in by_role.items():
        label = REF_TYPE_LABELS.get(role, REF_TYPE_LABELS["generic"])
        lines.append(f"- 用途为「{label}」的特征只取{'、'.join(f'图{i}' for i in idxs)}提供，其他图不得改变它；")
    lines.append("- 除声明用途为姿势/场景/构图/物品的图之外，姿势、角度、背景与光线一律全新创作；")
    if avoid:
        lines.append("用户划除的锚点/禁止项（不作为保留项）：" + "、".join(avoid))
    return "\n".join(lines)


def build_multi_reference_suffix(
    items: list[dict[str, Any]],
    avoid: Optional[list[str]] = None,
) -> str:
    """多图最终提示词硬约束后缀（防翻译官漏掉分工约束）。"""
    avoid = avoid or []
    lines = ["（参考图硬性要求，必须遵守）"]
    lines.append(f"多图分工：共 {len(items)} 张参考图，各图只提供自己声明的用途特征，互不借用：")
    for idx, it in enumerate(items, 1):
        role = str(it.get("type") or "generic")
        tpl = REFERENCE_TEMPLATES.get(role) or REFERENCE_TEMPLATES["generic"]
        label = REF_TYPE_LABELS.get(role, REF_TYPE_LABELS["generic"])
        preserve = str(it.get("preserve") or "").strip()
        keep = tpl["keep"]
        if preserve:
            keep += "\n关键特征（必须逐项保留）：" + preserve
        lines.append(f"图{idx}（{label}）：{keep}")
    lines.append("除声明用途为姿势/场景/构图/物品的图之外，姿势、角度、背景与光线一律全新创作。")
    if avoid:
        lines.append("用户划除（不作为保留项）：" + "、".join(avoid) + "；画面中不要出现上述元素。")
    return "\n".join(lines)


VISION_SCRIPT_ENV = "IMAGEGEN_VISION_SCRIPT"


def find_vision_bridge(cfg: Optional[dict[str, Any]] = None) -> str:
    """定位 vision_bridge.py：配置 > 环境变量 > PATH（Core 不扫描任何宿主目录）。"""
    if cfg:
        ref_cfg = cfg.get("reference") or {}
        if isinstance(ref_cfg, dict):
            configured = str(ref_cfg.get("vision_script") or "").strip()
            if configured:
                p = Path(configured).expanduser()
                if p.is_file():
                    return str(p)
    env_script = os.environ.get(VISION_SCRIPT_ENV, "").strip()
    if env_script:
        p = Path(env_script).expanduser()
        if p.is_file():
            return str(p)
    found = shutil.which("vision_bridge.py")
    return found or ""


CLASSIFY_QUESTION = (
    "请判断这张图片作为生图参考图时主要属于哪一类："
    "角色人物、服装造型、艺术风格、场景背景、构图布局、姿势动作、物品产品。"
    "只回答 JSON，不要其他文字："
    '{"type":"character|outfit|style|scene|composition|pose|object",'
    '"preserve":"一句话列出这张图里最需要保持的特征"}'
)

_TYPE_MAP = {
    "character": "character",
    "角色人物": "character",
    "角色": "character",
    "outfit": "outfit",
    "服装造型": "outfit",
    "服装": "outfit",
    "造型": "outfit",
    "style": "style",
    "艺术风格": "style",
    "风格": "style",
    "scene": "scene",
    "场景背景": "scene",
    "场景": "scene",
    "背景": "scene",
    "composition": "composition",
    "构图布局": "composition",
    "构图": "composition",
    "pose": "pose",
    "姿势动作": "pose",
    "姿势": "pose",
    "object": "object",
    "物品产品": "object",
    "物品": "object",
    "产品": "object",
}


def _parse_classify_result(raw: str) -> Optional[dict[str, str]]:
    """解析视觉桥接返回的类型 JSON；兼容整段 JSON 或夹在文字里的 JSON。"""
    if not raw:
        return None
    candidates = [raw.strip()]
    block = re.search(r"\{.*\}", raw, re.S)
    if block:
        candidates.append(block.group(0))
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        raw_type = str(data.get("type") or "").strip().lower()
        ref_type = _TYPE_MAP.get(raw_type)
        if not ref_type:
            continue
        return {
            "type": ref_type,
            "preserve": str(data.get("preserve") or "").strip(),
        }
    return None


def classify_reference(image: str, cfg: dict[str, Any]) -> dict[str, Any]:
    """调用 vision_bridge 自动分类参考图；任何失败都返回 ok=False 供降级。"""
    ref_cfg = cfg.get("reference") or {}
    if not isinstance(ref_cfg, dict):
        ref_cfg = {}
    if not bool(ref_cfg.get("auto_classify", True)):
        return {"ok": False, "type": "", "preserve": "", "method": "disabled",
                "error": "auto_classify=false，已跳过自动分类"}
    script = find_vision_bridge(cfg)
    if not script:
        return {"ok": False, "type": "", "preserve": "", "method": "fallback",
                "error": "vision_bridge 未找到，已降级为文字判断"}
    timeout = int(ref_cfg.get("classify_timeout") or 90)
    try:
        proc = subprocess.run(
            [sys.executable, script, "ask", image, CLASSIFY_QUESTION, "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "type": "", "preserve": "", "method": "fallback",
                "error": f"vision_bridge 调用失败：{exc}"}
    if proc.returncode != 0:
        return {"ok": False, "type": "", "preserve": "", "method": "fallback",
                "error": f"vision_bridge 退出码 {proc.returncode}：{proc.stderr.strip()[:200]}"}
    try:
        data = json.loads(proc.stdout)
        raw = str((data or {}).get("result") or "").strip()
    except (json.JSONDecodeError, TypeError):
        raw = proc.stdout.strip()
    parsed = _parse_classify_result(raw)
    if not parsed:
        return {"ok": False, "type": "", "preserve": "", "method": "fallback",
                "error": "视觉分类结果无法解析"}
    return {
        "ok": True,
        "type": parsed["type"],
        "preserve": parsed.get("preserve", ""),
        "method": "vision",
        "error": "",
    }


def resolve_ref_type(
    manual: str = "",
    user_text: str = "",
    classify: Optional[dict[str, Any]] = None,
) -> tuple[str, str]:
    """确定最终参考图类型：手动 > 视觉自动 > 降级 generic。"""
    manual = (manual or "").strip().lower()
    if manual and manual != "auto":
        return validate_ref_type(manual), "manual"
    if classify and classify.get("ok") and classify.get("type"):
        return str(classify["type"]), "vision"
    return "generic", "fallback"
