"""Backend API v1：vertex / openai-compatible（extra_backends 由它承接）。"""

from __future__ import annotations

from .base import BACKEND_API_VERSION, BackendCapabilities, ImageBackend
from .registry import get_backend, list_backends, register_backend

__all__ = [
    "BACKEND_API_VERSION",
    "BackendCapabilities",
    "ImageBackend",
    "get_backend",
    "list_backends",
    "register_backend",
]
