"""路由分发：METHOD + PATH 轻量匹配；HTTP 层只做协议适配，不实现业务规则。"""

from __future__ import annotations

import mimetypes
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote

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


def handle_index(context: Any, body: bytes = b"") -> Response:
    """GET / → standalone WebUI 首页。"""
    from ..web import asset_content_type, read_index_html

    return Response(
        200,
        read_index_html(),
        content_type=asset_content_type("index.html"),
    )


def handle_asset(context: Any, name: str, body: bytes = b"") -> Response:
    """GET /assets/{name} → 只允许白名单内的静态资源。"""
    from ..web import asset_content_type, read_asset

    try:
        data = read_asset(name)
    except FileNotFoundError:
        return error_response(404, "not_found", "asset not found")
    return Response(200, data, content_type=asset_content_type(name))


def handle_output(context: Any, generation_id: str) -> Response:
    """只允许读取当前 Server 注册过或历史记录里的合法生成结果，不提供任意文件读取。"""
    path = context.output_registry.get(generation_id)
    if path is None and context.history_service is not None:
        record = context.history_service.get(generation_id)
        if record is not None:
            path = record.output_path
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


def history_public_dict(record: Any) -> dict[str, Any]:
    """历史记录对外契约：不暴露 output_path，使用 output_url。"""
    return {
        "generation_id": record.id,
        "created_at": record.created_at,
        "prompt": record.prompt,
        "backend": record.backend,
        "image_model_used": record.image_model_used,
        "seed": record.seed,
        "requested_size": record.requested_size,
        "actual_size": record.actual_size,
        "output_url": f"/api/v1/outputs/{record.id}",
    }


def _query_int(
    query: dict[str, list[str]],
    name: str,
    default: int,
    min_value: int | None = None,
    max_value: int | None = None,
) -> int:
    raw = (query.get(name) or [None])[0]
    if raw is None or str(raw) == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ApiError(400, "validation_error", f"{name} must be an integer") from exc
    if min_value is not None and value < min_value:
        raise ApiError(400, "validation_error", f"{name} must be >= {min_value}")
    if max_value is not None and value > max_value:
        raise ApiError(400, "validation_error", f"{name} must be <= {max_value}")
    return value


def handle_history_list(context: Any, query: dict[str, list[str]]) -> Response:
    q = ((query.get("q") or [""])[0] or "").strip()
    limit = _query_int(query, "limit", 50, 1, 100)
    offset = _query_int(query, "offset", 0, 0)
    records = context.history_service.list(query=q, limit=limit, offset=offset)
    items = [history_public_dict(record) for record in records]
    return json_response(200, {"items": items, "count": len(items)})


def handle_history_get(context: Any, generation_id: str) -> Response:
    record = context.history_service.get(generation_id)
    if record is None:
        return error_response(404, "not_found", "unknown generation_id")
    return json_response(200, {"item": history_public_dict(record)})


def handle_history_delete(context: Any, generation_id: str) -> Response:
    deleted = context.history_service.delete(generation_id)
    context.output_registry.unregister(generation_id)
    if not deleted:
        return error_response(404, "not_found", "unknown generation_id")
    return json_response(200, {"deleted": True, "generation_id": generation_id})


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
    path_part, _, query_string = path.partition("?")
    query = parse_qs(query_string)
    handler = _STATIC_ROUTES.get((method, path_part))
    if handler is not None:
        return _safe_call(handler, context, body)
    allowed = [m for (m, p) in _STATIC_ROUTES if p == path_part]
    if allowed:
        return _method_not_allowed(allowed)

    match = re.fullmatch(r"/api/v1/backends/([^/]+)/models", path_part)
    if match:
        if method != "GET":
            return _method_not_allowed(["GET"])
        return _safe_call(handle_backend_models, context, unquote(match.group(1)))
    match = re.fullmatch(r"/api/v1/backends/([^/]+)", path_part)
    if match:
        if method != "GET":
            return _method_not_allowed(["GET"])
        return _safe_call(handle_backend_info, context, unquote(match.group(1)))

    if method == "GET" and path_part == "/api/v1/history":
        return _safe_call(handle_history_list, context, query)
    match = re.fullmatch(r"/api/v1/history/([^/]+)", path_part)
    if match:
        generation_id = unquote(match.group(1))
        if method == "GET":
            return _safe_call(handle_history_get, context, generation_id)
        if method == "DELETE":
            return _safe_call(handle_history_delete, context, generation_id)
        return _method_not_allowed(["GET", "DELETE"])

    match = re.fullmatch(r"/api/v1/outputs/([^/]+)", path_part)
    if match:
        if method != "GET":
            return _method_not_allowed(["GET"])
        return _safe_call(handle_output, context, unquote(match.group(1)))

    if method == "GET" and path_part == "/":
        return _safe_call(handle_index, context, body)
    match = re.fullmatch(r"/assets/([^/]+)", path_part)
    if match:
        if method != "GET":
            return _method_not_allowed(["GET"])
        return _safe_call(handle_asset, context, unquote(match.group(1)))

    return error_response(404, "not_found", "not found")
