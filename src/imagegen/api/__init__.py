"""Local HTTP API v1：纯协议适配器（不包含任何生图业务规则）。"""

from __future__ import annotations

from .outputs import OutputRegistry
from .server import create_server, validate_bind_address

HTTP_API_VERSION = 1

__all__ = [
    "HTTP_API_VERSION",
    "OutputRegistry",
    "create_server",
    "validate_bind_address",
]
