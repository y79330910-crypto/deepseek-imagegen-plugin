"""ImageGen 统一数据模型（稳定的序列化契约）。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping

from .errors import ValidationError


@dataclass
class GenerateRequest:
    """一次生图的完整输入。"""

    prompt: str
    size: str = ""
    width: int | None = None
    height: int | None = None
    model: str = ""
    backend: str = ""
    seed: int | None = None
    quality: str = ""
    composition: str = "auto"
    translator: str = "auto"
    size_policy: str = ""
    images: list[str] = field(default_factory=list)
    reference_roles: list[str] = field(default_factory=list)
    # 兼容字段（保留旧 CLI 参数）
    ref_type: str = "auto"
    library_enabled: bool | None = None
    out: str = ""
    denoise: float | None = None  # deprecated：当前后端不使用去噪强度，仅记录并忽略

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GenerateRequest":
        """从 JSON-safe dict 构造请求（不依赖 CLI / HTTP / Codex）。

        未知字段抛 ValidationError；缺失字段使用数据模型默认值。
        """
        if not isinstance(data, Mapping):
            raise ValidationError("GenerateRequest 需要 dict/Mapping 输入。")
        known = set(cls.__dataclass_fields__)
        unknown = [str(k) for k in data if k not in known]
        if unknown:
            raise ValidationError(f"未知字段：{', '.join(sorted(unknown))}")

        def as_str(value: Any, name: str) -> str:
            if not isinstance(value, str):
                raise ValidationError(f"{name} 必须是字符串。")
            return value

        def as_optional_int(value: Any, name: str) -> int | None:
            if value is None:
                return None
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValidationError(f"{name} 必须是整数或 null。")
            return value

        def as_str_list(value: Any, name: str) -> list[str]:
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ValidationError(f"{name} 必须是字符串数组。")
            return list(value)

        def as_optional_bool(value: Any, name: str) -> bool | None:
            if value is None:
                return None
            if not isinstance(value, bool):
                raise ValidationError(f"{name} 必须是布尔值或 null。")
            return value

        def as_optional_float(value: Any, name: str) -> float | None:
            if value is None:
                return None
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValidationError(f"{name} 必须是数字或 null。")
            return float(value)

        return cls(
            prompt=as_str(data.get("prompt", ""), "prompt"),
            size=as_str(data.get("size", ""), "size"),
            width=as_optional_int(data.get("width"), "width"),
            height=as_optional_int(data.get("height"), "height"),
            model=as_str(data.get("model", ""), "model"),
            backend=as_str(data.get("backend", ""), "backend"),
            seed=as_optional_int(data.get("seed"), "seed"),
            quality=as_str(data.get("quality", ""), "quality"),
            composition=as_str(data.get("composition", "auto"), "composition"),
            translator=as_str(data.get("translator", "auto"), "translator"),
            size_policy=as_str(data.get("size_policy", ""), "size_policy"),
            images=as_str_list(data.get("images", []), "images"),
            reference_roles=as_str_list(data.get("reference_roles", []), "reference_roles"),
            ref_type=as_str(data.get("ref_type", "auto"), "ref_type"),
            library_enabled=as_optional_bool(data.get("library_enabled"), "library_enabled"),
            out=as_str(data.get("out", ""), "out"),
            denoise=as_optional_float(data.get("denoise"), "denoise"),
        )

    def to_dict(self) -> dict[str, Any]:
        """稳定、JSON-safe 的输出（不包含 Path / bytes / callable 等对象）。"""
        return {
            "prompt": self.prompt,
            "size": self.size,
            "width": self.width,
            "height": self.height,
            "model": self.model,
            "backend": self.backend,
            "seed": self.seed,
            "quality": self.quality,
            "composition": self.composition,
            "translator": self.translator,
            "size_policy": self.size_policy,
            "images": list(self.images),
            "reference_roles": list(self.reference_roles),
            "ref_type": self.ref_type,
            "library_enabled": self.library_enabled,
            "out": self.out,
            "denoise": self.denoise,
        }


@dataclass
class GenerateResult:
    """一次生图的完整结果。"""

    path: str
    backend: str
    image_model_used: str
    seed: int | None
    requested_size: str
    actual_size: str
    prompt_used: str
    generation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    warnings: list[str] = field(default_factory=list)
    # 附加字段（CLI / WebUI 兼容）
    ok: bool = True
    model: str = ""
    quality: str = ""
    size_hint: str = ""
    size_match: bool = False
    size_check: dict[str, Any] = field(default_factory=dict)
    composition: str = "auto"
    composition_preset: str = "auto"
    bytes: int = 0
    translator: dict[str, Any] = field(default_factory=dict)
    reference: dict[str, Any] = field(default_factory=dict)
    prompt_library: dict[str, Any] = field(default_factory=dict)
    init_images: list[str] = field(default_factory=list)
    mirror_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转换为旧的 dict 结果格式（JSON 输出兼容）。"""
        data: dict[str, Any] = {
            "ok": self.ok,
            "backend": self.backend,
            "model": self.image_model_used,
            "image_model_used": self.image_model_used,
            "generation_id": self.generation_id,
            "quality": self.quality,
            "size_hint": self.size_hint,
            "path": self.path,
            "seed": self.seed,
            "size": self.requested_size,
            "actual_size": self.actual_size,
            "size_match": self.size_match,
            "size_check": self.size_check,
            "composition": self.composition,
            "composition_preset": self.composition_preset,
            "bytes": self.bytes,
            "translator": self.translator,
            "prompt_used": self.prompt_used,
            "reference": self.reference,
            "prompt_library": self.prompt_library,
            "warnings": list(self.warnings),
        }
        if self.init_images:
            data["init_images"] = list(self.init_images)
        if self.mirror_path:
            data["mirror_path"] = self.mirror_path
        return data
