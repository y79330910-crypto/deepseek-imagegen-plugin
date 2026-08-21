"""出图编排：GenerateRequest → 配置 → 参考图 → 构图 → 翻译官/词库 → Backend → 尺寸校验 → 保存 → GenerateResult。"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any, Optional

from . import reference as ref_mod
from .backends.openai_images import (
    discover_extra_backend,
    extra_backend_sizes,
    extra_size_aspect,
    normalize_extra_size,
    pick_extra_model_for_size,
)
from .backends.registry import get_backend
from .composition import composition_prompt_suffix, resolve_composition
from .config import load_config
from .errors import BackendError, ConfigurationError, GenError
from .image_utils import (
    aspect_ratio_key,
    canvas_size_for,
    default_output_path,
    load_init_image,
    mirror_output,
    parse_size,
    probe_image_size_ext,
    size_matches,
    sizes_match,
)
from .models import (
    GenerateRequest,
    GenerateResult,
    normalize_size_policy,
    validate_backend_request,
)
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
    except LibError as exc:
        return [], [], str(exc)
    except Exception as exc:  # noqa: BLE001
        return [], [], f"词库检索异常：{exc}"


def _run_generation(
    request: GenerateRequest, explicit_config: Optional[dict[str, Any]]
) -> GenerateResult:
    """出图主流程实现（Engine 编排 + Backend API v1）。"""
    request.validate()
    prompt = (request.prompt or "").strip()
    warnings: list[str] = []
    if request.denoise is not None:
        warnings.append("denoise 参数已弃用：当前后端不使用去噪强度，该参数已被忽略。")
    cfg = explicit_config if explicit_config is not None else load_config()

    # ---- 构图预设：未指定尺寸时采用预设画幅
    comp_preset = resolve_composition(request.composition, cfg)
    comp_suffix = composition_prompt_suffix(comp_preset, cfg)

    # ---- 尺寸策略（代码默认：auto / 重试 2 次 / 容差 6%）
    raw_policy = (
        request.size_policy
        or str(cfg.get("size_policy", {}).get("mode") or "auto")
    )
    policy, policy_warnings = normalize_size_policy(raw_policy)
    warnings.extend(policy_warnings)
    sp_cfg = cfg.get("size_policy") or {}
    tolerance = float(sp_cfg.get("tolerance", 0.06) or 0.06)
    size_retries = max(0, int(sp_cfg.get("retries", 2) or 0))
    empty_retries = 2
    retry_delay_base = 6.0

    # ---- 出图后端：默认 vertex（本地代理）；其他名称走 OpenAI 兼容备用后端
    # 尽早选择并校验能力，避免不支持的请求走到参考图/翻译官阶段
    backend_name = (request.backend or "").strip().lower()
    if backend_name in ("", "vertex"):
        backend_name = "vertex"
    backend = get_backend(backend_name, cfg)
    if backend.id == "openai-compatible" and not getattr(backend, "name", ""):
        raise GenError(
            "openai-compatible 是通用后端名，请指定 extra_backends 中配置的具体后端名"
            "（如 dragtokens）。"
        )
    validate_backend_request(request, backend.capabilities())

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
            elif len(user_refs) == 1 and request.ref_type not in ("", "auto"):
                explicit = request.ref_type
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
    engine_name = str(request.translator or "auto")
    if engine_name in ("", "auto"):
        engine_name = str(tr.get("engine") or "deepseek")
    tr_enabled = engine_name.strip().lower() not in ("off", "none", "direct", "直传")

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
            user_prompt, cfg=cfg, engine=engine_name, examples=examples,
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

    # ---- 尺寸确定：--size 字符串 > 显式宽高 > 构图预设画幅 > 参考图原尺寸 > 配置默认
    size_value = request.size.strip()
    if size_value and size_value.lower() != "auto":
        width, height = parse_size(size_value)
    elif request.width is not None and request.height is not None:
        width, height = int(request.width), int(request.height)
    else:
        preset_size = ""
        if comp_preset != "auto":
            preset_cfg = (cfg.get("composition") or {}).get("presets") or {}
            preset_size = str((preset_cfg.get(comp_preset) or {}).get("size") or "")
        if preset_size:
            width, height = parse_size(preset_size)
        elif init_images_data:
            probed = probe_image_size_ext(init_images_data[0][0], init_images_data[0][1])
            width, height = probed or parse_size("1024x1024")
        else:
            width, height = parse_size(str(cfg.get("default_size") or "1024x1024"))

    seed = request.seed if request.seed is not None else random.randint(0, 2**31 - 1)
    used_canvas_first = False
    size_actions: list[str] = []

    eff_model = (request.model or "").strip()
    size_str = ""
    aspect = ""
    size_hint = ""
    extra_prompt = ""
    if backend.id == "vertex":
        eff_model = backend.resolve_model(cfg, eff_model)
    else:
        if not eff_model:
            auto_model = pick_extra_model_for_size(cfg, backend_name, width, height)
            if auto_model:
                eff_model = auto_model
                warnings.append(f"已按尺寸自动选择备用后端模型：{auto_model}")
        size_str = normalize_extra_size(
            width, height, extra_backend_sizes(cfg, backend_name, eff_model)
        )
        aspect = extra_size_aspect(size_str)
        if size_str != f"{width}x{height}".lower():
            warnings.append(f"备用后端按白名单把尺寸调整为 {size_str}（最接近的可用档位）。")
        size_hint = (
            f"（画面尺寸要求：生成 {aspect}、{size_str} 尺寸的图片）"
            if aspect
            else f"（画面尺寸要求：{size_str} 尺寸的图片）"
        )
        extra_prompt = final_prompt.strip() + "\n" + size_hint
        eff_model = backend.resolve_model(cfg, eff_model)

    # ---- 生成
    if init_images_data:
        if backend.id == "vertex":
            data = backend.edit(
                cfg, final_prompt.strip(), width, height, eff_model, init_images_data
            )
        else:
            data = backend.edit(
                cfg, extra_prompt, width, height, eff_model, init_images_data,
                size_str=size_str, quality=request.quality,
            )
    elif backend.id == "vertex":
        if aspect_ratio_key(width, height) == (3, 2) or (width, height) == (1408, 768):
            data = backend.generate(
                cfg, final_prompt.strip(), width, height, eff_model,
                empty_retries=empty_retries, retry_delay_base=retry_delay_base,
            )
        else:
            data = backend.generate_fallback_size(
                cfg, final_prompt.strip(), width, height, eff_model,
                empty_retries=empty_retries, retry_delay_base=retry_delay_base,
            )
            used_canvas_first = True
    else:
        data = backend.generate(
            cfg, extra_prompt, width, height, eff_model,
            size_str=size_str, quality=request.quality,
            empty_retries=empty_retries, retry_delay_base=retry_delay_base,
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
    match_ok = size_matches(requested, actual_size, policy, tolerance)
    if (
        not match_ok
        and actual_size is not None
        and not init_images_data
        and backend_name == "vertex"
    ):
        canvas_w, canvas_h = canvas_size_for(width, height)
        for _attempt in range(max(1, size_retries + 1)):
            try:
                data = backend.generate_fallback_size(
                    cfg, final_prompt.strip(), width, height, eff_model,
                    empty_retries=empty_retries, retry_delay_base=retry_delay_base,
                )
                actual_size = probe_image_size_ext(data, "")
                match_ok = size_matches(requested, actual_size, policy, tolerance)
                used_canvas_first = True
                size_actions.append(f"画布优先重试（{canvas_w}x{canvas_h} 画布）")
                if match_ok:
                    break
            except GenError as exc:
                warnings.append(f"画布优先兜底失败：{str(exc)[:150]}")
                break
    if match_ok:
        if used_canvas_first:
            warnings.append("已启用画布优先：代理文生图不遵守尺寸，改用目标画幅画布出图。")
    else:
        reason = (
            sizes_match(requested, actual_size, tolerance).get("reason")
            if actual_size is not None
            else "无法读取生成图的真实尺寸"
        )
        if policy in ("aspect", "exact"):
            raise BackendError(
                f"尺寸策略为 {policy}：{reason}"
                + (f"，已尝试：{'；'.join(size_actions)}" if size_actions else "")
                + "。可改用 --size-policy auto 自动兜底。"
            )
        warnings.append(f"尺寸未完全匹配（{reason}），已按策略保留并如实记录实际尺寸。")

    # ---- 保存输出与镜像副本
    out_ext = "png"
    if request.out:
        out_path = Path(request.out).expanduser()
        if out_path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
            out_path = out_path / default_output_path(prompt, seed, cfg, ext=out_ext).name
    else:
        out_path = default_output_path(prompt, seed, cfg, ext=out_ext)
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
    except FileExistsError as exc:
        raise ConfigurationError(
            f"输出路径不可用：{out_path.parent} 位置存在同名文件，请更换 --out 路径。"
        ) from exc
    out_path.write_bytes(data)

    result = GenerateResult(
        path=str(out_path),
        backend=backend_name,
        image_model_used=eff_model,
        model=eff_model,
        seed=seed,
        requested_size=f"{width}x{height}",
        actual_size=f"{actual_size[0]}x{actual_size[1]}" if actual_size else "未知",
        prompt_used=final_prompt,
        warnings=warnings,
        quality=request.quality,
        size_hint=size_hint if backend_name != "vertex" else "",
        size_match=bool(actual_size and match_ok),
        size_check={
            "requested": f"{width}x{height}",
            "effective": size_str if backend_name != "vertex" else "",
            "actual": f"{actual_size[0]}x{actual_size[1]}" if actual_size else None,
            "match": bool(actual_size and match_ok),
            "reason": (
                sizes_match(requested, actual_size, tolerance).get("reason")
                if actual_size is not None
                else ""
            ),
            "canvas_first": used_canvas_first,
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
