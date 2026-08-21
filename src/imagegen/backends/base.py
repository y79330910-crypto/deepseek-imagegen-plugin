"""Backend API v1：轻量后端接口。

Backend 只负责“如何向具体图像服务发送请求”；完整业务流程由 Engine 编排。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..errors import GenError


BACKEND_API_VERSION = 1


@dataclass
class BackendCapabilities:
    text_to_image: bool = True
    image_to_image: bool = False
    multi_reference: bool = False
    quality: bool = False
    seed: bool = False
    exact_size: bool = False


class ImageBackend:
    """图像后端基类。"""

    id: str = ""
    api_version: int = BACKEND_API_VERSION

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities()

    def list_models(self, cfg: dict[str, Any]) -> list[str]:
        return []

    def resolve_model(self, cfg: dict[str, Any], requested: str = "") -> str:
        """返回本次生成实际使用的图像模型名（requested 优先，其次自动选择）。"""
        return (requested or "").strip()

    def generate(
        self,
        cfg: dict[str, Any],
        prompt: str,
        width: int,
        height: int,
        model: str = "",
        **kwargs: Any,
    ) -> bytes:
        """文生图：返回图片字节。"""
        raise GenError(f"后端「{self.id}」不支持文生图。")

    def generate_fallback_size(
        self,
        cfg: dict[str, Any],
        prompt: str,
        width: int,
        height: int,
        model: str = "",
        **kwargs: Any,
    ) -> bytes:
        """尺寸不符时的兜底生成策略；默认与 generate 相同，后端可覆盖。"""
        return self.generate(cfg, prompt, width, height, model=model, **kwargs)

    def edit(
        self,
        cfg: dict[str, Any],
        prompt: str,
        width: int,
        height: int,
        model: str,
        images: list[tuple[bytes, str, str]],
        **kwargs: Any,
    ) -> bytes:
        """图生图 / 参考图：images 为 [(bytes, mime, name), ...]。"""
        raise GenError(f"后端「{self.id}」不支持图生图。")
