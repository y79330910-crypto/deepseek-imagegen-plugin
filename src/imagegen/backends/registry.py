"""Backend 注册表：代码内静态注册，不扫描插件目录、不加载第三方 Python。"""

from __future__ import annotations

from typing import Any, Optional

from .base import ImageBackend


_REGISTRY: dict[str, ImageBackend] = {}


def register_backend(
    backend: ImageBackend, aliases: Optional[list[str]] = None
) -> None:
    """注册后端实例（可按别名重复注册）。"""
    if not backend.id:
        raise ValueError("backend.id 不能为空")
    _REGISTRY[backend.id] = backend
    for alias in aliases or []:
        if alias:
            _REGISTRY[alias] = backend


def get_backend(
    name: str = "", config: Optional[dict[str, Any]] = None
) -> ImageBackend:
    """按名称取后端：vertex / openai-compatible / extra。

    未注册的名称视为 extra_backends 里动态配置的备用后端名，
    统一由 OpenAI 兼容后端承接（不加载任何第三方 Python）。
    """
    key = (name or "").strip().lower()
    if key in ("", "vertex"):
        key = "vertex"
    backend = _REGISTRY.get(key)
    if backend is not None:
        return backend
    from .openai_images import OpenAIImagesBackend

    return OpenAIImagesBackend(key)


def list_backends() -> list[str]:
    """返回已注册后端 id 列表。"""
    return sorted({backend.id for backend in _REGISTRY.values()})


def _register_defaults() -> None:
    from .openai_images import OpenAIImagesBackend
    from .vertex import VertexBackend

    register_backend(VertexBackend())
    register_backend(OpenAIImagesBackend(""), aliases=["extra"])


_register_defaults()
