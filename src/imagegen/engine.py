"""出图编排：GenerateRequest → 配置 → 参考图 → 构图 → 翻译官/词库 → 图像上游 → 尺寸检查 → 保存。

提示词与图像使用两套独立 OpenAI-Compatible 上游，尺寸原样透传，输出尺寸不符只加 warning。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from . import reference as ref_mod
from .composition import composition_prompt_suffix, resolve_composition
from .config import load_config
from .errors import ConfigurationError
from .image_utils import (
    default_output_path,
    load_init_image,
    mirror_output,
    parse_size,
    probe_image_size_ext,
    sizes_match,
)
from .models import GenerateRequest, GenerateResult
from .openai_client import OpenAIClient
from .translator import translate_prompt


def _library_search(
    user_prompt: str, cfg: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[str], str]:
    """词库检索：返回 (命中摘要, 示例文本, 警告)。"""
    pl = cfg.get("prompt_library") or {}
    if not isinstance(pl, dict):
        pl = {}
    try:
        from .library import LibError, load_config as lib_load, search as lib_search

        lib_cfg = lib_load()
        hits = lib_search(
            lib_cfg,
            user_prompt,
            top_k=int(pl.get("top_k") or 30),
            final_k=int(pl.get("final_k") or 6),
            categories=pl.get("categories") or None,
        )
        examples = [str(h.get("content") or "") for h in hits if h.get("content")]
        summary = [
            {"id": h.get("id"), "category": h.get("category") or ""} for h in hits
        ]
        return summary, examples, ""
    except Exception as exc:  # noqa: BLE001
        return [], [], f"词库检索异常：{exc}"


def _resolve_size(
    request: GenerateRequest,
    cfg: dict[str, Any],
    comp_preset: str,
    init_images_data: list[tuple[bytes, str, str]],
) -> tuple[int, int]:
    """确定目标尺寸：--size > 构图预设画幅 > 参考图原尺寸 > 配置默认。"""
    size_value = str(request.size or "").strip()
    if size_value and size_value.lower() != "auto":
        return parse_size(size_value)
    if comp_preset != "auto":
        preset_cfg = (cfg.get("composition") or {}).get("presets") or {}
        preset_size = str((preset_cfg.get(comp_preset) or {}).get("size") or "")
        if preset_size:
            return parse_size(preset_size)
    if init_images_data:
        probed = probe_image_size_ext(init_images_data[0][0], init_images_data[0][1])
        if probed:
            return probed
    return parse_size(str(cfg.get("default_size") or "1024x1024"))


def _run_generation(
    request: GenerateRequest, explicit_config: Optional[dict[str, Any]]
) -> GenerateResult:
    """出图主流程实现。"""
    request.validate()
    prompt = (request.prompt or "").strip()
    warnings: list[str] = []
    cfg = explicit_config if explicit_config is not None else load_config()

    # ---- 构图预设：未指定尺寸时采用预设画幅
    comp_preset = resolve_composition(request.composition, cfg)
    comp_suffix = composition_prompt_suffix(comp_preset, cfg)

    # ---- 参考图（支持多图）：提取禁止项、逐张识别用途并生成分工简报
    avoid_items = ref_mod.detect_avoid_items(prompt)
    ref_brief = ""
    ref_info: dict[str, Any] = {
        "type": "",
        "label": "",
        "method": "",
        "preserve": "",
        "avoid": avoid_items,
        "brief": "",
        "items": [],
    }
    user_refs = [str(p or "").strip().strip('"') for p in (request.images or [])]
    user_refs = [p for p in user_refs if p]
    if user_refs:
        roles_in = [str(r or "").strip().lower() for r in (request.reference_roles or [])]
        ref_items: list[dict[str, Any]] = []
        for i, path in enumerate(user_refs):
            explicit = ""
            if i < len(roles_in):
                explicit = roles_in[i]
            feat = ref_mod.extract_reference_features(path, explicit, cfg)
            rtype = str(feat.get("type") or "").strip()
            if not rtype or rtype == "auto":
                rtype = "character" if i == 0 else ref_mod.AUTO_ROLE_ORDER[
                    min(i - 1, len(ref_mod.AUTO_ROLE_ORDER) - 1)
                ]
            ref_items.append({
                "path": path,
                "type": rtype,
                "label": ref_mod.REF_TYPE_LABELS.get(
                    rtype, ref_mod.REF_TYPE_LABELS["generic"]
                ),
                "method": str(feat.get("method") or "manual"),
                "preserve": str(feat.get("preserve") or "").strip(),
            })
        if len(ref_items) == 1:
            it = ref_items[0]
            identity_list = it["preserve"] if it["type"] in ("character", "generic") else ""
            ref_brief = ref_mod.build_reference_brief(
                it["type"], prompt, it["preserve"], avoid_items, identity_list
            )
            ref_info = {
                "type": it["type"],
                "label": it["label"],
                "method": it["method"],
                "preserve": it["preserve"],
                "identity_list": identity_list,
                "avoid": avoid_items,
                "brief": ref_brief,
                "items": ref_items,
            }
        else:
            ref_brief = ref_mod.build_multi_reference_brief(ref_items, avoid_items)
            ref_info = {
                "type": ref_items[0]["type"],
                "label": ref_items[0]["label"],
                "method": "multi",
                "preserve": "",
                "avoid": avoid_items,
                "brief": ref_brief,
                "items": ref_items,
            }

    # ---- 翻译官输入 = 用户需求 + 构图约束 + 参考图简报
    user_prompt = prompt
    if comp_suffix:
        user_prompt += "\n【构图要求】" + comp_suffix
    if ref_brief:
        user_prompt += "\n\n" + ref_brief

    tr = cfg.get("translator") or {}
    if not isinstance(tr, dict):
        tr = {}
    tr_mode = str(request.translator or "auto").strip().lower()
    tr_enabled = bool(tr.get("enabled", True)) if tr_mode == "auto" else False

    # ---- 词库检索（仅翻译官开启时喂示例）
    tr_info: dict[str, Any] = {
        "ok": True,
        "model": "",
        "original": prompt,
        "rewritten": prompt,
    }
    lib_hits: list[dict[str, Any]] = []
    lib_warning = ""
    pl_cfg = cfg.get("prompt_library") or {}
    lib_enabled = (
        request.library_enabled
        if request.library_enabled is not None
        else bool(pl_cfg.get("enabled", False))
    )
    if tr_enabled and lib_enabled and bool(pl_cfg.get("use_in_translator", True)):
        lib_hits, examples, lib_warning = _library_search(user_prompt, cfg)
        if lib_warning:
            tr_info["library_warning"] = lib_warning
        tr_info["library_hits"] = lib_hits
    else:
        examples: list[str] = []

    if tr_enabled:
        tr_info = translate_prompt(
            user_prompt, cfg=cfg, examples=examples, reference_brief=ref_brief,
        )
        tr_info["library_hits"] = lib_hits
        final_prompt = tr_info.get("rewritten") or prompt
    else:
        final_prompt = user_prompt
    # 最终提示词再补一遍硬约束（防止翻译官漏掉构图/参考图约束）
    if comp_suffix:
        final_prompt += "\n（构图硬性要求）" + comp_suffix
    if ref_brief:
        if len(ref_info.get("items") or []) > 1:
            final_prompt += "\n" + ref_mod.build_multi_reference_suffix(
                ref_info["items"], avoid_items
            )
        else:
            final_prompt += "\n" + ref_mod.build_reference_suffix(
                ref_info["type"], avoid_items, ref_info.get("identity_list") or ""
            )

    # ---- 参考图：用户 --image（可多张；读不了时按错误处理）
    init_images_data: list[tuple[bytes, str, str]] = []
    if user_refs:
        for _p in user_refs:
            init_images_data.append(load_init_image(_p))

    # ---- 尺寸：用户尺寸原样透传，不做白名单 / 归一化
    width, height = _resolve_size(request, cfg, comp_preset, init_images_data)
    size_str = f"{width}x{height}"

    # ---- 图像上游（image.* 独立 OpenAI-Compatible 连接）
    img_cfg = cfg.get("image") or {}
    if not isinstance(img_cfg, dict):
        img_cfg = {}
    base_url = str(img_cfg.get("base_url") or "").strip()
    api_key = str(img_cfg.get("api_key") or "").strip()
    eff_model = (request.model or "").strip() or str(img_cfg.get("model") or "").strip()
    if not base_url or not api_key:
        raise ConfigurationError(
            "image.base_url / image.api_key 未配置：请在设置页填写图像 OpenAI API。"
        )
    if not eff_model:
        raise ConfigurationError(
            "image.model 未配置：请在设置页填写图像模型，或使用「拉取模型」。"
        )
    quality = (request.quality or "").strip() or str(img_cfg.get("quality") or "").strip()
    client = OpenAIClient(base_url, api_key)
    if init_images_data:
        data = client.edit_image(
            eff_model, final_prompt.strip(), size_str, init_images_data,
            quality=quality,
        )
    else:
        data = client.generate_image(
            eff_model, final_prompt.strip(), size_str, quality=quality
        )

    # ---- 尺寸检查：读取真实输出尺寸，不符只加 warning，不自动重试 / 不修改请求
    actual_size = probe_image_size_ext(data, "")
    sc = cfg.get("size_check") or {}
    if not isinstance(sc, dict):
        sc = {}
    size_check_enabled = bool(sc.get("enabled", True))
    tolerance = float(sc.get("tolerance", 0.06) or 0.06)
    match_ok = bool(actual_size and sizes_match((width, height), actual_size, tolerance).get("ok"))
    reason = ""
    if not match_ok:
        reason = (
            sizes_match((width, height), actual_size, tolerance).get("reason")
            if actual_size is not None
            else "无法读取生成图的真实尺寸"
        )
        if size_check_enabled:
            actual_text = (
                f"{actual_size[0]}x{actual_size[1]}" if actual_size else "未知"
            )
            warnings.append(
                f"输出尺寸与请求不符（请求 {size_str}，实际 {actual_text}）：{reason}"
            )

    # ---- 保存输出与镜像副本
    out_ext = "png"
    if request.out:
        out_path = Path(request.out).expanduser()
        if out_path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
            out_path = out_path / default_output_path(prompt, cfg, ext=out_ext).name
    else:
        out_path = default_output_path(prompt, cfg, ext=out_ext)
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
    except FileExistsError as exc:
        raise ConfigurationError(
            f"输出路径不可用：{out_path.parent} 位置存在同名文件，请更换 --out 路径。"
        ) from exc
    out_path.write_bytes(data)

    result = GenerateResult(
        path=str(out_path),
        image_model_used=eff_model,
        requested_size=size_str,
        actual_size=f"{actual_size[0]}x{actual_size[1]}" if actual_size else "未知",
        prompt_used=final_prompt,
        warnings=warnings,
        quality=quality,
        size_match=match_ok,
        size_check={
            "requested": size_str,
            "actual": f"{actual_size[0]}x{actual_size[1]}" if actual_size else None,
            "match": match_ok,
            "reason": reason,
        },
        composition=request.composition,
        composition_preset=comp_preset,
        bytes=len(data),
        translator=tr_info,
        reference=ref_info,
        prompt_library={"enabled": lib_enabled, "hits": lib_hits},
    )
    if init_images_data:
        result.init_images = list(user_refs)
    mirror = mirror_output(str(out_path), cfg)
    if mirror:
        result.mirror_path = mirror
    return result


class ImageGenEngine:
    """生图业务编排引擎：只负责业务流程，不包含 CLI / HTTP / UI 逻辑。"""

    def __init__(self, config: Optional[dict[str, Any]] = None):
        """可选显式配置；缺省走 load_config()（环境变量 > 用户配置 > 默认值）。"""
        self._config = config

    def generate(self, request: GenerateRequest) -> GenerateResult:
        """执行一次生图请求，返回 GenerateResult。"""
        return _run_generation(request, self._config)


def generate(request: GenerateRequest) -> GenerateResult:
    """兼容旧入口：等价于 ImageGenEngine().generate(request)。"""
    return ImageGenEngine().generate(request)
