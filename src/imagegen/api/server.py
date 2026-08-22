"""Local HTTP API v2 server：ThreadingHTTPServer + 轻量 handler。"""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

from ..errors import ConfigurationError
from ..services import (
    AssetService,
    ConfigService,
    DiagnosticService,
    GenerationService,
    HistoryService,
    ModelService,
    ReferenceResolver,
)
from . import routes
from .outputs import OutputRegistry
from .responses import ApiError

LOGGER = logging.getLogger("imagegen.api")

MAX_BODY_BYTES = 1024 * 1024  # 1 MB（JSON 接口）
MAX_UPLOAD_BYTES = 32 * 1024 * 1024  # 32 MB（multipart 上传接口）


def validate_bind_address(host: str, allow_remote: bool = False) -> str:
    """默认只允许 loopback；非 loopback 必须显式 --allow-remote。"""
    host = (host or "").strip()
    loopback = {"127.0.0.1", "localhost", "::1", ""}
    if host in loopback:
        return host or "127.0.0.1"
    if not allow_remote:
        raise ConfigurationError(
            f"Remote binding to '{host}' requires --allow-remote. "
            "--allow-remote exposes the ImageGen API to the network."
        )
    return host


class ApiContext:
    """Server 共享上下文：所有 route 使用同一配置上下文。"""

    def __init__(
        self,
        config_service: ConfigService,
        generation_service: GenerationService,
        model_service: ModelService,
        diagnostic_service: DiagnosticService,
        output_registry: OutputRegistry,
        history_service: HistoryService,
        asset_service: AssetService,
        reference_resolver: ReferenceResolver,
    ):
        self.config_service = config_service
        self.generation_service = generation_service
        self.model_service = model_service
        self.diagnostic_service = diagnostic_service
        self.output_registry = output_registry
        self.history_service = history_service
        self.asset_service = asset_service
        self.reference_resolver = reference_resolver
        self.generation_lock = threading.Lock()


class ApiHandler(BaseHTTPRequestHandler):
    server_version = "ImageGenHTTP/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        LOGGER.debug("request: %s", fmt % args)

    def _read_body(self, max_bytes: int) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length > max_bytes:
            # 读取并丢弃前 MAX+1 字节，避免客户端仍在发送时连接被粗暴中断
            self.rfile.read(max_bytes + 1)
            raise ApiError(
                400,
                "payload_too_large",
                f"request body exceeds {max_bytes} bytes",
            )
        return self.rfile.read(length)

    def _handle(self, method: str, has_body: bool) -> None:
        try:
            limit = (
                MAX_UPLOAD_BYTES
                if method == "POST" and self.path.startswith("/api/v2/assets")
                else MAX_BODY_BYTES
            )
            body = self._read_body(limit) if has_body else b""
            response = routes.dispatch(
                self.server.context,
                method,
                self.path,
                body,
                dict(self.headers),
            )
        except ApiError as exc:
            response = routes.error_response(exc.status, exc.error_type, exc.message)
        except Exception:  # noqa: BLE001
            LOGGER.exception("unhandled error in api handler")
            response = routes.error_response(500, "internal_error", "internal server error")
        self._write(response)

    def _write(self, response: routes.Response) -> None:
        cache_control = response.headers.pop("Cache-Control", "no-store")
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Cache-Control", cache_control)
        for key, value in response.headers.items():
            self.send_header(key, value)
        if isinstance(response.body, bytes):
            payload = response.body
        elif isinstance(response.body, str):
            payload = response.body.encode("utf-8")
        else:
            payload = json.dumps(response.body, ensure_ascii=False).encode("utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET", has_body=False)

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST", has_body=True)

    def do_PATCH(self) -> None:  # noqa: N802
        self._handle("PATCH", has_body=True)

    def do_PUT(self) -> None:  # noqa: N802
        self._handle("PUT", has_body=True)

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle("DELETE", has_body=False)


def create_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    config_path: str | Path | None = None,
    config_service: Optional[ConfigService] = None,
    generation_service: Optional[GenerationService] = None,
    model_service: Optional[ModelService] = None,
    diagnostic_service: Optional[DiagnosticService] = None,
    output_registry: Optional[OutputRegistry] = None,
    history_service: Optional[HistoryService] = None,
    history_db_path: str | Path | None = None,
    asset_service: Optional[AssetService] = None,
    asset_dir: str | Path | None = None,
) -> ThreadingHTTPServer:
    """构建 HTTP API v2 server；services 缺省时基于同一 config_path 创建。"""
    cfg_service = config_service or ConfigService(config_path)
    db_path = history_db_path or (cfg_service.path().parent / "imagegen.db")
    if history_service is None:
        history_service = HistoryService(db_path=db_path)
    if asset_service is None:
        asset_service = AssetService(
            db_path=getattr(history_service, "db_path", db_path),
            asset_dir=asset_dir
            or (cfg_service.path().parent / "assets" / "references"),
        )
    reference_resolver = ReferenceResolver(asset_service)
    if generation_service is None:
        generation_service = GenerationService(
            config_service=cfg_service, history_service=history_service
        )
    if model_service is None:
        model_service = ModelService(config_service=cfg_service)
    if diagnostic_service is None:
        diagnostic_service = DiagnosticService(config_path=cfg_service.path())
    registry = output_registry or OutputRegistry()
    context = ApiContext(
        config_service=cfg_service,
        generation_service=generation_service,
        model_service=model_service,
        diagnostic_service=diagnostic_service,
        output_registry=registry,
        history_service=history_service,
        asset_service=asset_service,
        reference_resolver=reference_resolver,
    )
    server = ThreadingHTTPServer((host, port), ApiHandler)
    server.daemon_threads = True
    server.context = context
    return server
