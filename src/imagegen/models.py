"""ImageGen 统一数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class GenerateRequest:
    """一次生图的完整输入。"""

    prompt: str
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
        }
        if self.init_images:
            data["init_images"] = list(self.init_images)
        if self.warnings:
            data["warnings"] = list(self.warnings)
        if self.mirror_path:
            data["mirror_path"] = self.mirror_path
        return data
