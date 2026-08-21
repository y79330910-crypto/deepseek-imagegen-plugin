"""路由分发：METHOD + PATH 轻量匹配；HTTP 层只做协议适配，不实现业务规则。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import unquote

from ..backends.base import BACKEND_API_VERSION
from ..errors import BackendError, ConfigurationError, ImageGenError, ValidationError
from .responses import ApiError, parse_json_payload

HTTP_API_VERSION = 1


@dataclass
class Response:
    status: int
    body: Any
    content_type: str = "application/json; charset=utf-8"
    headers: dict[str, str] = field(default_factory=dict)


def json_response(status: int, payload: dict[str, Any]) -> Response:
    return Response(status, payload)


def error_response(status: int, error_type: str, message: str) -> Response:
    return Response(status, {"error": {"type": error_type, "message": message}})


def map_exception(exc: BaseException) -> Response:
    """Core 异常 → HTTP 错误契约；不暴露 Python 类名 / traceback。"""
    if isinstance(exc, ApiError):
        return error_response(exc.status, exc.error_type, exc.message)
    if isinstance(exc, ValidationError):
        return error_response(400, "validation_error", str(exc))
    if isinstance(exc, ConfigurationError):
        return error_response(400, "configuration_error", str(exc))
    if isinstance(exc, BackendError):
        return error_response(502, "backend_error", str(exc))
    if isinstance(exc, ImageGenError):
        return error_response(500, "imagegen_error", str(exc))
    return error_response(500, "internal_error", "internal server error")


def handle_health(context: Any) -> Response:
    """轻量健康检查：只表示 Server 与 Core 已启动，不访问任何后端。"""
    from .. import CORE_API_VERSION

    return json_response(
        200,
        {
            "status": "ok",
            "api_version": HTTP_API_VERSION,
            "core_api_version": CORE_API_VERSION,
            "backend_api_version": BACKEND_API_VERSION,
        },
    )


_STATIC_ROUTES: dict[tuple[str, str], Callable[..., Response]] = {
    ("GET", "/api/v1/health"): handle_health,
}


def _safe_call(handler: Callable[..., Response], *args: Any) -> Response:
    try:
        return handler(*args)
    except Exception as exc:  # noqa: BLE001
        return map_exception(exc)


def _method_not_allowed(allowed: list[str]) -> Response:
    return Response(
        405,
        {"error": {"type": "method_not_allowed", "message": "method not allowed"}},
        headers={"Allow": ", ".join(sorted(allowed))},
    )


def dispatch(context: Any, method: str, path: str, body: bytes) -> Response:
    handler = _STATIC_ROUTES.get((method, path))
    if handler is not None:
        return _safe_call(handler, context)
    allowed = [m for (m, p) in _STATIC_ROUTES if p == path]
    if allowed:
        return _method_not_allowed(allowed)

    match = re.fullmatch(r"/api/v1/backends/([^/]+)/models", path)
    if match:
        if method != "GET":
            return _method_not_allowed(["GET"])
        return _safe_call(handle_backend_models, context, unquote(match.group(1)))
    match = re.fullmatch(r"/api/v1/backends/([^/]+)", path)
    if match:
        if method != "GET":
            return _method_not_allowed(["GET"])
        return _safe_call(handle_backend_info, context, unquote(match.group(1)))
    match = re.fullmatch(r"/api/v1/outputs/([^/]+)", path)
    if match:
        if method != "GET":
            return _method_not_allowed(["GET"])
        return _safe_call(handle_output, context, unquote(match.group(1)))
    return error_response(404, "not_found", "not found")
