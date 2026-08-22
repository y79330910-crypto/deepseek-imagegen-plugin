"""路由分发：METHOD + PATH 轻量匹配；HTTP 层只做协议适配，不实现业务规则。"""

from __future__ import annotations

import mimetypes
import re
from dataclasses import dataclass, field
from email.utils import formatdate
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, unquote

from ..errors import (
    AssetInUseError,
    AssetNotFoundError,
    ConfigurationError,
    ImageGenError,
    UpstreamError,
    ValidationError,
)
from ..models import GenerateRequest
from ..services.previews import PreviewError
from .responses import ApiError, parse_json_payload

HTTP_API_VERSION = 2


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
    if isinstance(exc, UpstreamError):
        return error_response(502, "upstream_error", str(exc))
    if isinstance(exc, AssetNotFoundError):
        return error_response(404, "not_found", str(exc))
    if isinstance(exc, AssetInUseError):
        return error_response(409, "asset_in_use", str(exc))
    if isinstance(exc, PreviewError):
        return error_response(404, "not_found", str(exc))
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
        },
    )


def handle_models(context: Any, body: bytes) -> Response:
    """POST /api/v2/models：按 target 拉取提示词 / 图像上游模型。"""
    payload = parse_json_payload(body)
    target = str(payload.get("target") or "").strip()
    models = context.model_service.list_models(target)
    return json_response(200, {"models": models})


def handle_generate(context: Any, body: bytes) -> Response:
    payload = parse_json_payload(body)
    resolver = getattr(context, "reference_resolver", None)
    if resolver is not None:
        resolved = resolver.resolve(payload)
    else:
        resolved = dict(payload)
        resolved.pop("references", None)
    request = GenerateRequest.from_dict(resolved)
    with context.generation_lock:
        result = context.generation_service.generate(request)
    context.output_registry.register(result.generation_id, result.path)
    _attach_asset_references(context, result, payload)
    response = result.to_dict()
    # 安全边界：HTTP 不暴露服务器本地文件路径，统一使用 output_url。
    response.pop("path", None)
    response.pop("mirror_path", None)
    response["output_url"] = f"/api/v2/outputs/{result.generation_id}"
    return json_response(200, response)


def _attach_asset_references(context: Any, result: Any, payload: dict[str, Any]) -> None:
    """Asset Reference 生成成功后 best-effort 记录 generation_assets；失败不改变结果。"""
    asset_service = getattr(context, "asset_service", None)
    references = payload.get("references") or []
    if asset_service is None or not references:
        return
    for position, ref in enumerate(references):
        try:
            asset_id = str(ref.get("asset_id") or "").strip()
            role = str(ref.get("role") or "auto").strip() or "auto"
            asset_service.attach_to_generation(
                result.generation_id, asset_id, role, position
            )
        except Exception as exc:  # noqa: BLE001
            result.warnings.append(f"reference relation persistence failed: {exc}")


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


def _resolve_output_path(context: Any, generation_id: str) -> Optional[str]:
    """generation_id → 已知输出路径（registry / history），不提供任意文件读取。"""
    path = context.output_registry.get(generation_id)
    if path is None and context.history_service is not None:
        record = context.history_service.get(generation_id)
        if record is not None:
            path = record.output_path
    return path


def _conditional_file_response(
    file_path: Path,
    request_headers: dict[str, str],
    content_type: str,
    cache_control: str,
) -> Response:
    """ETag / Last-Modified / Cache-Control + If-None-Match → 304 空 body。"""
    stat = file_path.stat()
    etag = f'"{stat.st_size}-{stat.st_mtime_ns}"'
    last_modified = formatdate(stat.st_mtime, usegmt=True)
    headers = {
        "ETag": etag,
        "Last-Modified": last_modified,
        "Cache-Control": cache_control,
    }
    if request_headers.get("If-None-Match") == etag:
        return Response(304, b"", content_type=content_type, headers=headers)
    try:
        data = file_path.read_bytes()
    except OSError:
        return error_response(404, "not_found", "file not found")
    return Response(200, data, content_type=content_type, headers=headers)


def handle_output(
    context: Any, generation_id: str, headers: Optional[dict[str, str]] = None
) -> Response:
    """只允许读取当前 Server 注册过或历史记录里的合法生成结果，不提供任意文件读取。"""
    path = _resolve_output_path(context, generation_id)
    if path is None:
        return error_response(404, "not_found", "unknown generation_id")
    file_path = Path(path)
    if not file_path.is_file():
        return error_response(404, "not_found", "output file not found")
    content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    return _conditional_file_response(
        file_path,
        headers or {},
        content_type=content_type,
        cache_control="private, max-age=3600",
    )


def handle_generation_thumbnail(
    context: Any, generation_id: str, headers: Optional[dict[str, str]] = None
) -> Response:
    """GET /api/v2/outputs/{generation_id}/thumbnail → 懒生成 WebP 缩略图。"""
    path = _resolve_output_path(context, generation_id)
    if path is None:
        return error_response(404, "not_found", "unknown generation_id")
    file_path = Path(path)
    if not file_path.is_file():
        return error_response(404, "not_found", "output file not found")
    preview_service = getattr(context, "preview_service", None)
    if preview_service is None:
        return error_response(500, "imagegen_error", "preview service unavailable")
    try:
        thumb = preview_service.generation_thumbnail(generation_id, file_path)
    except PreviewError as exc:
        return error_response(404, "not_found", str(exc))
    return _conditional_file_response(
        thumb,
        headers or {},
        content_type="image/webp",
        cache_control="private, max-age=31536000, immutable",
    )


def history_public_dict(record: Any) -> dict[str, Any]:
    """历史记录对外契约：不暴露 output_path，使用 output_url。"""
    return {
        "generation_id": record.id,
        "created_at": record.created_at,
        "prompt": record.prompt,
        "image_model_used": record.image_model_used,
        "seed": record.seed,
        "requested_size": record.requested_size,
        "actual_size": record.actual_size,
        "output_url": f"/api/v2/outputs/{record.id}",
        "thumbnail_url": f"/api/v2/outputs/{record.id}/thumbnail",
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
    records = context.history_service.list(query=q, limit=limit + 1, offset=offset)
    items = [history_public_dict(record) for record in records]
    return json_response(200, _paged_response(items, offset, limit))


def _paged_response(items: list[Any], offset: int, limit: int) -> dict[str, Any]:
    """分页响应：limit+1 探测 has_more，next_offset 仅在有更多时给出。"""
    has_more = len(items) > limit
    page = items[:limit]
    return {
        "items": page,
        "count": len(page),
        "has_more": has_more,
        "next_offset": offset + len(page) if has_more else None,
    }


def handle_history_get(context: Any, generation_id: str) -> Response:
    record = context.history_service.get(generation_id)
    if record is None:
        return error_response(404, "not_found", "unknown generation_id")
    item = history_public_dict(record)
    item["prompt_used"] = record.prompt_used
    item["warnings"] = list(record.warnings)
    # request 只公开 allow-list 字段；旧记录里的本机路径（images/out 等）绝不外泄。
    item["request"] = _safe_request_public(record.request or {})
    references, ref_warnings = _history_references(context, generation_id)
    item["references"] = references
    item["warnings"].extend(ref_warnings)
    return json_response(200, {"item": item})


HISTORY_REQUEST_ALLOW = (
    "prompt",
    "size",
    "model",
    "seed",
    "quality",
    "composition",
    "translator",
    "library_enabled",
)


def _safe_request_public(request: dict[str, Any]) -> dict[str, Any]:
    """只公开可安全恢复的生成参数，禁止 images / reference_roles / out 等路径字段。"""
    return {key: request[key] for key in HISTORY_REQUEST_ALLOW if key in request}


def _history_references(
    context: Any, generation_id: str
) -> tuple[list[dict[str, Any]], list[str]]:
    """History detail 的 references：来自 generation_assets（position ASC）。

    某条历史 asset 已不存在时跳过该引用并返回 warning，不导致 detail 失败。
    """
    asset_service = getattr(context, "asset_service", None)
    if asset_service is None:
        return [], []
    links = asset_service.list_for_generation(generation_id)
    references: list[dict[str, Any]] = []
    warnings: list[str] = []
    for link in links:
        record = asset_service.get(link.asset_id)
        if record is None:
            warnings.append(f"reference asset missing: {link.asset_id}")
            continue
        references.append({
            "asset_id": link.asset_id,
            "role": link.role,
            "position": link.position,
            "content_url": f"/api/v2/assets/{link.asset_id}/content",
            "thumbnail_url": f"/api/v2/assets/{link.asset_id}/thumbnail",
        })
    return references, warnings


def handle_history_delete(context: Any, generation_id: str) -> Response:
    deleted = context.history_service.delete(generation_id)
    context.output_registry.unregister(generation_id)
    if not deleted:
        return error_response(404, "not_found", "unknown generation_id")
    preview_service = getattr(context, "preview_service", None)
    if preview_service is not None:
        # 删除历史 ≠ 删除输出图片；只清理对应 generation preview 缓存
        preview_service.invalidate_generation(generation_id)
    return json_response(200, {"deleted": True, "generation_id": generation_id})


def handle_assets_create(
    context: Any, body: bytes, content_type: str = ""
) -> Response:
    """POST /api/v2/assets：multipart/form-data 文件上传。"""
    from .multipart import parse_multipart

    try:
        parts = parse_multipart(body, content_type)
    except ValueError as exc:
        raise ApiError(400, "validation_error", str(exc)) from exc
    file_part = next((p for p in parts if p.name == "file"), None)
    if file_part is None or not file_part.data:
        raise ApiError(400, "validation_error", "missing file field")
    kind = next(
        (
            p.data.decode("utf-8", "replace").strip()
            for p in parts
            if p.name == "kind"
        ),
        "reference",
    )
    record = context.asset_service.create_from_upload(
        data=file_part.data,
        original_name=file_part.filename or "",
        kind=kind or "reference",
        mime_type=file_part.content_type or "",
    )
    return Response(201, record.to_public_dict())


def handle_assets_import(context: Any, body: bytes) -> Response:
    """POST /api/v2/assets/import：服务器本机路径导入。"""
    payload = parse_json_payload(body)
    path = str(payload.get("path") or "").strip()
    if not path:
        raise ApiError(400, "validation_error", "path is required")
    kind = str(payload.get("kind") or "reference").strip() or "reference"
    record = context.asset_service.import_path(path, kind=kind)
    return Response(201, record.to_public_dict())


def handle_assets_list(context: Any, query: dict[str, list[str]]) -> Response:
    kind = (query.get("kind") or [""])[0] or None
    q = ((query.get("q") or [""])[0] or "").strip()
    limit = _query_int(query, "limit", 50, 1, 100)
    offset = _query_int(query, "offset", 0, 0)
    records = context.asset_service.list(
        kind=kind, query=q, limit=limit + 1, offset=offset
    )
    items = [record.to_public_dict() for record in records]
    return json_response(200, _paged_response(items, offset, limit))


def handle_assets_get(context: Any, asset_id: str) -> Response:
    record = context.asset_service.get(asset_id)
    if record is None:
        return error_response(404, "not_found", "unknown asset_id")
    return json_response(200, record.to_public_dict())


def handle_assets_content(
    context: Any, asset_id: str, headers: Optional[dict[str, str]] = None
) -> Response:
    record = context.asset_service.get(asset_id)
    if record is None:
        return error_response(404, "not_found", "unknown asset_id")
    file_path = Path(context.asset_service.resolve_path(asset_id))
    content_type = record.mime_type or "application/octet-stream"
    return _conditional_file_response(
        file_path,
        headers or {},
        content_type=content_type,
        cache_control="private, max-age=31536000, immutable",
    )


def handle_asset_thumbnail(
    context: Any, asset_id: str, headers: Optional[dict[str, str]] = None
) -> Response:
    """GET /api/v2/assets/{asset_id}/thumbnail → 懒生成 WebP 缩略图。"""
    record = context.asset_service.get(asset_id)
    if record is None:
        return error_response(404, "not_found", "unknown asset_id")
    file_path = Path(context.asset_service.resolve_path(asset_id))
    preview_service = getattr(context, "preview_service", None)
    if preview_service is None:
        return error_response(500, "imagegen_error", "preview service unavailable")
    try:
        thumb = preview_service.asset_thumbnail(asset_id, file_path)
    except PreviewError as exc:
        return error_response(404, "not_found", str(exc))
    return _conditional_file_response(
        thumb,
        headers or {},
        content_type="image/webp",
        cache_control="private, max-age=31536000, immutable",
    )


def handle_assets_delete(context: Any, asset_id: str) -> Response:
    try:
        deleted = context.asset_service.delete(asset_id)
    except AssetInUseError as exc:
        return error_response(409, "asset_in_use", str(exc))
    if not deleted:
        return error_response(404, "not_found", "unknown asset_id")
    preview_service = getattr(context, "preview_service", None)
    if preview_service is not None:
        preview_service.invalidate_asset(asset_id)
    return json_response(200, {"deleted": True, "asset_id": asset_id})


_STATIC_ROUTES: dict[tuple[str, str], Callable[..., Response]] = {
    ("GET", "/api/v2/health"): handle_health,
    ("POST", "/api/v2/models"): handle_models,
    ("POST", "/api/v2/generate"): handle_generate,
    ("GET", "/api/v2/config"): handle_get_config,
    ("PATCH", "/api/v2/config"): handle_patch_config,
    ("POST", "/api/v2/doctor"): handle_doctor,
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


def dispatch(
    context: Any,
    method: str,
    path: str,
    body: bytes,
    headers: dict[str, str] | None = None,
) -> Response:
    path_part, _, query_string = path.partition("?")
    query = parse_qs(query_string)
    headers = headers or {}
    handler = _STATIC_ROUTES.get((method, path_part))
    if handler is not None:
        return _safe_call(handler, context, body)
    allowed = [m for (m, p) in _STATIC_ROUTES if p == path_part]
    if allowed:
        return _method_not_allowed(allowed)

    if method == "GET" and path_part == "/api/v2/history":
        return _safe_call(handle_history_list, context, query)
    match = re.fullmatch(r"/api/v2/history/([^/]+)", path_part)
    if match:
        generation_id = unquote(match.group(1))
        if method == "GET":
            return _safe_call(handle_history_get, context, generation_id)
        if method == "DELETE":
            return _safe_call(handle_history_delete, context, generation_id)
        return _method_not_allowed(["GET", "DELETE"])

    if path_part == "/api/v2/assets":
        if method == "GET":
            return _safe_call(handle_assets_list, context, query)
        if method == "POST":
            return _safe_call(
                handle_assets_create,
                context,
                body,
                headers.get("Content-Type", ""),
            )
        return _method_not_allowed(["GET", "POST"])

    if method == "POST" and path_part == "/api/v2/assets/import":
        return _safe_call(handle_assets_import, context, body)

    match = re.fullmatch(r"/api/v2/assets/([^/]+)/thumbnail", path_part)
    if match:
        if method != "GET":
            return _method_not_allowed(["GET"])
        return _safe_call(
            handle_asset_thumbnail,
            context,
            unquote(match.group(1)),
            headers,
        )

    match = re.fullmatch(r"/api/v2/assets/([^/]+)/content", path_part)
    if match:
        if method != "GET":
            return _method_not_allowed(["GET"])
        return _safe_call(
            handle_assets_content,
            context,
            unquote(match.group(1)),
            headers,
        )

    match = re.fullmatch(r"/api/v2/assets/([^/]+)", path_part)
    if match:
        asset_id = unquote(match.group(1))
        if method == "GET":
            return _safe_call(handle_assets_get, context, asset_id)
        if method == "DELETE":
            return _safe_call(handle_assets_delete, context, asset_id)
        return _method_not_allowed(["GET", "DELETE"])

    match = re.fullmatch(r"/api/v2/outputs/([^/]+)", path_part)
    if match:
        if method != "GET":
            return _method_not_allowed(["GET"])
        return _safe_call(handle_output, context, unquote(match.group(1)), headers)

    match = re.fullmatch(r"/api/v2/outputs/([^/]+)/thumbnail", path_part)
    if match:
        if method != "GET":
            return _method_not_allowed(["GET"])
        return _safe_call(
            handle_generation_thumbnail,
            context,
            unquote(match.group(1)),
            headers,
        )

    if method == "GET" and path_part == "/":
        return _safe_call(handle_index, context, body)
    match = re.fullmatch(r"/assets/([^/]+)", path_part)
    if match:
        if method != "GET":
            return _method_not_allowed(["GET"])
        return _safe_call(handle_asset, context, unquote(match.group(1)))

    return error_response(404, "not_found", "not found")
