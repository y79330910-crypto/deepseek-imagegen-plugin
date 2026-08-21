"""路由分发：METHOD + PATH 轻量匹配；HTTP 层只做协议适配，不实现业务规则。"""

from __future__ import annotations

import mimetypes
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote

from ..backends.base import BACKEND_API_VERSION
from ..errors import BackendError, ConfigurationError, ImageGenError, ValidationError
from ..models import GenerateRequest
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


def handle_health(context: Any, body: bytes = b"") -> Response:
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


def handle_backends(context: Any, body: bytes = b"") -> Response:
    return json_response(200, {"backends": context.model_service.list_backends()})


def handle_backend_info(context: Any, backend_id: str) -> Response:
    if not context.model_service.backend_exists(backend_id):
        return error_response(404, "not_found", f"unknown backend: {backend_id}")
    return json_response(200, context.model_service.get_backend_info(backend_id))


def handle_backend_models(context: Any, backend_id: str) -> Response:
    if not context.model_service.backend_exists(backend_id):
        return error_response(404, "not_found", f"unknown backend: {backend_id}")
    return json_response(
        200,
        {"backend": backend_id, "models": context.model_service.list_models(backend_id)},
    )


def handle_generate(context: Any, body: bytes) -> Response:
    payload = parse_json_payload(body)
    request = GenerateRequest.from_dict(payload)
    with context.generation_lock:
        result = context.generation_service.generate(request)
    context.output_registry.register(result.generation_id, result.path)
    response = result.to_dict()
    response["output_url"] = f"/api/v1/outputs/{result.generation_id}"
    return json_response(200, response)


def handle_get_config(context: Any, body: bytes = b"") -> Response:
    return json_response(200, {"config": context.config_service.masked()})


def handle_patch_config(context: Any, body: bytes) -> Response:
    payload = parse_json_payload(body)
    result = context.config_service.update(payload)
    return json_response(200, {"config": result})


def handle_doctor(context: Any, body: bytes = b"") -> Response:
    return json_response(200, context.diagnostic_service.doctor())


def handle_output(context: Any, generation_id: str) -> Response:
    """只允许读取当前 Server 注册过的生成结果，不提供任意文件读取。"""
    path = context.output_registry.get(generation_id)
    if path is None:
        return error_response(404, "not_found", "unknown generation_id")
    file_path = Path(path)
    if not file_path.is_file():
        return error_response(404, "not_found", "output file not found")
    try:
        data = file_path.read_bytes()
    except OSError:
        return error_response(404, "not_found", "output file not found")
    content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    return Response(
        200,
        data,
        content_type=content_type,
        headers={"Cache-Control": "private"},
    )


_STATIC_ROUTES: dict[tuple[str, str], Callable[..., Response]] = {
    ("GET", "/api/v1/health"): handle_health,
    ("GET", "/api/v1/backends"): handle_backends,
    ("POST", "/api/v1/generate"): handle_generate,
    ("GET", "/api/v1/config"): handle_get_config,
    ("PATCH", "/api/v1/config"): handle_patch_config,
    ("POST", "/api/v1/doctor"): handle_doctor,
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
        return _safe_call(handler, context, body)
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
