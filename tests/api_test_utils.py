"""HTTP API 集成测试公共工具（mock Service，不访问真实后端）。"""

from __future__ import annotations

import json
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

from imagegen import ConfigService
from imagegen.api import create_server
from imagegen.models import GenerateResult


class FakeGenerationService:
    def __init__(
        self,
        result: Optional[GenerateResult] = None,
        exc: Optional[Exception] = None,
    ):
        self.result = result or GenerateResult(
            path=r"D:\tmp\out.png",
            backend="vertex",
            image_model_used="gemini-3-pro-image",
            seed=1,
            requested_size="1024x1024",
            actual_size="1024x1024",
            prompt_used="p",
            warnings=["w1"],
        )
        self.exc = exc
        self.last_request = None

    def generate(self, request):
        self.last_request = request
        if self.exc is not None:
            raise self.exc
        return self.result


class FakeModelService:
    def __init__(
        self,
        backends: Optional[list[dict]] = None,
        models: Optional[list[str]] = None,
        info: Optional[dict] = None,
    ):
        self.backends = backends or [
            {
                "id": "vertex",
                "api_version": 1,
                "capabilities": {"text_to_image": True, "image_to_image": True},
            }
        ]
        self.models = models or ["gemini-3-pro-image"]
        self.info = info or {
            "id": "vertex",
            "api_version": 1,
            "capabilities": {},
            "models": self.models,
            "best_model": "gemini-3-pro-image",
            "base_url": "http://127.0.0.1:2156/v1",
        }

    def list_backends(self):
        return self.backends

    def backend_exists(self, backend_id: str) -> bool:
        return backend_id in {"vertex", "openai-compatible", "dragtokens"}

    def get_backend_info(self, backend_id: str):
        return self.info

    def list_models(self, backend_id: str):
        return self.models


class FakeDiagnosticService:
    def __init__(self, result: Optional[dict] = None, exc: Optional[Exception] = None):
        self.result = result or {"ok": True, "backend": "vertex", "checks": []}
        self.exc = exc

    def doctor(self, size_probe: bool = False, size: str = ""):
        if self.exc is not None:
            raise self.exc
        return self.result


class ApiTestServer:
    """起一个随机端口、临时配置、可注入 mock Service 的测试 server。"""

    def __init__(
        self,
        config_path: Optional[Path] = None,
        initial_config: Optional[dict] = None,
        **service_overrides: Any,
    ):
        self.tmp = tempfile.TemporaryDirectory()
        self.add_cleanup = self.tmp.cleanup
        self.config_path = config_path or Path(self.tmp.name) / "config.json"
        if initial_config:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text(
                json.dumps(initial_config), encoding="utf-8"
            )
        services: dict[str, Any] = {
            "config_service": ConfigService(self.config_path),
            "generation_service": FakeGenerationService(),
            "model_service": FakeModelService(),
            "diagnostic_service": FakeDiagnosticService(),
        }
        services.update(service_overrides)
        self.server = create_server(
            "127.0.0.1", 0, config_path=self.config_path, **services
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"

    def request(self, method: str, path: str, body: Any = None, headers: Optional[dict] = None):
        data = None
        request_headers = dict(headers or {})
        if body is not None:
            if isinstance(body, (dict, list)):
                data = json.dumps(body).encode("utf-8")
            elif isinstance(body, str):
                data = body.encode("utf-8")
            else:
                data = body
            request_headers.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(
            self.base + path, data=data, method=method, headers=request_headers
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, exc.read()

    def json(self, method: str, path: str, body: Any = None, headers: Optional[dict] = None):
        status, response_headers, raw = self.request(method, path, body=body, headers=headers)
        return status, response_headers, json.loads(raw.decode("utf-8"))

    def close(self):
        try:
            self.server.shutdown()
        except Exception:  # noqa: BLE001
            pass
        self.server.server_close()
        self.thread.join(timeout=5)
        self.tmp.cleanup()
