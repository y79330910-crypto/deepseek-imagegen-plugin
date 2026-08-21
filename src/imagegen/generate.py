"""出图编排：参考图 → 词库检索 → 翻译官 → 构图预设 → Vertex 出图 → 尺寸校验。"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any, Optional

from . import reference as ref_mod
from .composition import (
    composition_checklist,
    composition_prompt_suffix,
    resolve_composition,
)
from .config import load_config
from .errors import GenError
from .image_utils import (
    aspect_ratio_key,
    default_output_path,
    load_init_image,
    mirror_output,
    parse_size,
    probe_image_size_ext,
    sizes_match,
)
from .translator import translate_prompt
from .vertex import (
    discover_extra_backend,
    extra_backend_sizes,
    extra_size_aspect,
    gen_extra_image,
    gen_extra_img2img,
    gen_vertex,
    gen_vertex_canvas_first,
    gen_vertex_img2img,
    normalize_extra_size,
    pick_extra_model_for_size,
)


def _library_search(user_prompt: str, cfg: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str], str]:
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
    except LibError as exc:
        return [], [], str(exc)
    except Exception as exc:  # noqa: BLE001
        return [], [], f"词库检索异常：{exc}"


def generate(
    prompt: str,
    *,
    out: Optional[str] = None,
    size: str = "",
    seed: Optional[int] = None,
    model: str = "",
    init_image: Optional[str] = None,
    init_images: Optional[list[str]] = None,
    ref_roles: Optional[list[str]] = None,
    denoise: Optional[float] = None,
    translator: str = "auto",
    composition: str = "auto",
    size_policy: str = "",
    backend: str = "",
    quality: str = "",
    library_enabled: Optional[bool] = None,
    ref_type: str = "auto",
) -> dict[str, Any]:
    """v1.0 出图主流程。"""
    if not prompt or not prompt.strip():
        raise GenError("提示词不能为空。")
    if denoise is not None and not (0 < denoise <= 1):
        raise GenError("--denoise 必须是 0~1 之间的小数（默认 0.6）。")
    cfg = load_config()
    warnings: list[str] = []

    # ---- 构图预设：未指定尺寸时采用预设画幅
    comp_preset = resolve_composition(composition, cfg)
    comp_suffix = composition_prompt_suffix(comp_preset, cfg)
    _checklist = composition_checklist(comp_preset, cfg)
    if not size.strip() and comp_preset != "auto":
        preset_cfg = (cfg.get("composition") or {}).get("presets") or {}
        preset_size = str((preset_cfg.get(comp_preset) or {}).get("size") or "")
        if preset_size:
            size = preset_size

    # ---- 尺寸策略（代码默认：auto / 重试 2 次 / 容差 6%）
    policy = (size_policy or str(cfg.get("size_policy", {}).get("mode") or "auto")).strip().lower()
    if policy not in ("strict", "auto", "warn"):
        policy = "auto"
    sp_cfg = cfg.get("size_policy") or {}
    tolerance = float(sp_cfg.get("tolerance", 0.06) or 0.06)
    size_retries = max(0, int(sp_cfg.get("retries", 2) or 0))
    empty_retries = 2
    retry_delay_base = 6.0

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
    user_refs = [str(p or "").strip().strip('"') for p in (init_images or [])]
    if not user_refs and (init_image or "").strip():
        user_refs = [str(init_image).strip().strip('"')]
    user_refs = [p for p in user_refs if p]
    if len(user_refs) > ref_mod.MAX_REF_IMAGES:
        raise GenError(
            f"参考图最多支持 {ref_mod.MAX_REF_IMAGES} 张，当前收到 {len(user_refs)} 张。"
        )
    if user_refs:
        roles_in = [str(r or "").strip().lower() for r in (ref_roles or [])]
        ref_items: list[dict[str, Any]] = []
        for i, path in enumerate(user_refs):
            explicit = roles_in[i] if i < len(roles_in) else ""
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
    engine = str(translator or "auto")
    if engine in ("", "auto"):
        engine = str(tr.get("engine") or "deepseek")
    tr_enabled = engine.strip().lower() not in ("off", "none", "direct", "直传")

    # ---- 词库检索（仅翻译官开启时喂示例）
    tr_info: dict[str, Any] = {
        "ok": True,
        "engine": "off",
        "engine_used": "off",
        "model": "",
        "original": prompt,
        "rewritten": prompt,
        "fallback": False,
    }
    lib_hits: list[dict[str, Any]] = []
    lib_warning = ""
    pl_cfg = cfg.get("prompt_library") or {}
    lib_enabled = library_enabled if library_enabled is not None else bool(pl_cfg.get("enabled", False))
    if tr_enabled and lib_enabled and bool(pl_cfg.get("use_in_translator", True)):
        lib_hits, examples, lib_warning = _library_search(user_prompt, cfg)
        if lib_warning:
            tr_info["library_warning"] = lib_warning
        tr_info["library_hits"] = lib_hits
    else:
        examples: list[str] = []

    if tr_enabled:
        tr_info = translate_prompt(
            user_prompt, cfg=cfg, engine=engine, examples=examples,
            reference_brief=ref_brief,
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
        if size:
            width, height = parse_size(size)
        else:
            probed = probe_image_size_ext(init_images_data[0][0], init_images_data[0][1])
            width, height = probed or parse_size("1024x1024")
    else:
        width, height = parse_size(size or str(cfg.get("default_size") or "1024x1024"))

    seed = seed if seed is not None else random.randint(0, 2**31 - 1)
    used_canvas_first = False
    size_actions: list[str] = []

    # ---- 出图后端：默认 vertex（本地代理）；其他走 extra_backends 备用后端
    backend_name = (backend or "").strip().lower()
    if backend_name in ("", "vertex"):
        backend_name = "vertex"
    if quality and backend_name == "vertex":
        warnings.append("quality 参数仅备用后端生效，本地 Vertex 出图已忽略。")
    if backend_name == "vertex":
        if init_images_data:
            data = gen_vertex_img2img(
                cfg,
                final_prompt.strip(),
                width,
                height,
                model,
                init_images_data,
            )
        else:
            if aspect_ratio_key(width, height) == (3, 2) or (width, height) == (1408, 768):
                data = gen_vertex(
                    cfg,
                    final_prompt.strip(),
                    width,
                    height,
                    model,
                    empty_retries=empty_retries,
                    retry_delay_base=retry_delay_base,
                )
            else:
                data = gen_vertex_canvas_first(
                    cfg,
                    final_prompt.strip(),
                    width,
                    height,
                    model,
                    empty_retries=empty_retries,
                    retry_delay_base=retry_delay_base,
                )
                used_canvas_first = True
    else:
        binfo = discover_extra_backend(cfg, backend_name)
        if not model:
            auto_model = pick_extra_model_for_size(cfg, backend_name, width, height)
            if auto_model:
                model = auto_model
                warnings.append(f"已按尺寸自动选择备用后端模型：{model}")
        size_str = normalize_extra_size(width, height, extra_backend_sizes(cfg, backend_name, model))
        aspect = extra_size_aspect(size_str)
        if size_str != f"{width}x{height}".lower():
            warnings.append(f"备用后端按白名单把尺寸调整为 {size_str}（最接近的可用档位）。")
        size_hint = (
            f"（画面尺寸要求：生成 {aspect}、{size_str} 尺寸的图片）"
            if aspect
            else f"（画面尺寸要求：{size_str} 尺寸的图片）"
        )
        extra_prompt = final_prompt.strip() + "\n" + size_hint
        if init_images_data:
            data = gen_extra_img2img(
                cfg,
                backend_name,
                extra_prompt,
                width,
                height,
                model,
                init_images_data,
                size_str=size_str,
                quality=quality,
            )
        else:
            data = gen_extra_image(
                cfg,
                backend_name,
                extra_prompt,
                width,
                height,
                model,
                size_str=size_str,
                quality=quality,
                empty_retries=empty_retries,
                retry_delay_base=retry_delay_base,
            )

    # ---- 真实尺寸校验 + 画布优先兜底（文生图且尺寸不符时重试）
    actual_size = probe_image_size_ext(data, "")
    if backend_name == "vertex":
        requested = (width, height)
    else:
        try:
            requested = parse_size(size_str)
        except Exception:  # noqa: BLE001
            requested = (width, height)
    match = sizes_match(requested, actual_size, tolerance)
    if (
        not match["ok"]
        and actual_size is not None
        and not init_images_data
        and backend_name == "vertex"
    ):
        from .image_utils import canvas_size_for

        canvas_w, canvas_h = canvas_size_for(width, height)
        for _attempt in range(max(1, size_retries + 1)):
            try:
                data = gen_vertex_canvas_first(
                    cfg,
                    final_prompt.strip(),
                    width,
                    height,
                    model,
                    empty_retries=empty_retries,
                    retry_delay_base=retry_delay_base,
                )
                actual_size = probe_image_size_ext(data, "")
                match = sizes_match(requested, actual_size, tolerance)
                used_canvas_first = True
                size_actions.append(f"画布优先重试（{canvas_w}x{canvas_h} 画布）")
                if match["ok"]:
                    break
            except GenError as exc:
                warnings.append(f"画布优先兜底失败：{str(exc)[:150]}")
                break
    if match["ok"]:
        if used_canvas_first:
            warnings.append("已启用画布优先：代理文生图不遵守尺寸，改用目标画幅画布出图。")
    else:
        reason = match.get("reason") or "尺寸不符"
        if policy == "strict":
            raise GenError(
                f"尺寸策略为 strict：{reason}"
                + (f"，已尝试：{'；'.join(size_actions)}" if size_actions else "")
                + "。可改用 --size-policy auto 自动兜底。"
            )
        warnings.append(f"尺寸未完全匹配（{reason}），已按策略保留并如实记录实际尺寸。")

    # ---- 保存输出与镜像副本
    out_ext = "png"
    if out:
        out_path = Path(out).expanduser()
        if out_path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
            out_path = out_path / default_output_path(prompt, seed, cfg, ext=out_ext).name
    else:
        out_path = default_output_path(prompt, seed, cfg, ext=out_ext)
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
    except FileExistsError as exc:
        raise GenError(
            f"输出路径不可用：{out_path.parent} 位置存在同名文件，请更换 --out 路径。"
        ) from exc
    out_path.write_bytes(data)

    result: dict[str, Any] = {
        "ok": True,
        "backend": backend_name,
        "model": model,
        "quality": quality,
        "size_hint": size_hint if backend_name != "vertex" else "",
        "path": str(out_path),
        "seed": seed,
        "size": f"{width}x{height}",
        "actual_size": f"{actual_size[0]}x{actual_size[1]}" if actual_size else "未知",
        "size_match": bool(actual_size and match["ok"]),
        "size_check": {
            "requested": f"{width}x{height}",
            "effective": size_str if backend_name != "vertex" else "",
            "actual": f"{actual_size[0]}x{actual_size[1]}" if actual_size else None,
            "match": bool(actual_size and match["ok"]),
            "reason": match.get("reason") or "",
            "canvas_first": used_canvas_first,
        },
        "composition": composition,
        "composition_preset": comp_preset,
        "bytes": len(data),
        "translator": tr_info,
        "prompt_used": final_prompt,
        "reference": ref_info,
        "prompt_library": {"enabled": lib_enabled, "hits": lib_hits},
    }
    if init_images_data:
        result["init_images"] = user_refs
        result["denoise"] = denoise if denoise is not None else 0.6
    if warnings:
        result["warnings"] = warnings
    mirror = mirror_output(str(out_path), cfg)
    if mirror:
        result["mirror_path"] = mirror
    return result
