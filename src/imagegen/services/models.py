"""ModelService：统一查询后端、模型与能力信息。"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Optional

from ..backends.registry import get_backend, list_backends
from ..backends.vertex import discover_vertex
from ..config import load_config
from ..errors import ImageGenError


class ModelService:
    """对外围程序提供结构化的后端 / 模型查询。"""

    def __init__(
        self,
        config: Optional[dict[str, Any]] = None,
        config_service: Optional[Any] = None,
    ):
        self._config = config
        self._config_service = config_service

    def _cfg(self) -> dict[str, Any]:
        if self._config_service is not None:
            return self._config_service.load()
        return self._config if self._config is not None else load_config()

    def list_backends(self) -> list[dict[str, Any]]:
        """返回后端清单：id / api_version / capabilities。"""
        result: list[dict[str, Any]] = []
        for backend_id in list_backends():
            backend = get_backend(backend_id)
            result.append(
                {
                    "id": backend.id,
                    "api_version": backend.api_version,
                    "capabilities": asdict(backend.capabilities()),
                }
            )
        return result

    def list_models(self, backend_id: str = "vertex") -> list[str]:
        """列出指定后端的可用模型。"""
        backend = get_backend(backend_id)
        return list(backend.list_models(self._cfg()))

    def get_backend_info(self, backend_id: str = "vertex") -> dict[str, Any]:
        """返回单个后端信息：id / capabilities / models / best_model / base_url。"""
        backend = get_backend(backend_id)
        models = list(backend.list_models(self._cfg()))
        best = ""
        try:
            best = backend.resolve_model(self._cfg(), "")
        except ImageGenError:
            best = ""
        info: dict[str, Any] = {
            "id": backend.id,
            "api_version": backend.api_version,
            "capabilities": asdict(backend.capabilities()),
            "models": models,
            "best_model": best,
            "base_url": "",
        }
        if backend.id == "vertex":
            try:
                info["base_url"] = discover_vertex(self._cfg()).get("base_url", "")
            except ImageGenError:
                info["base_url"] = ""
        return info

    def backend_exists(self, backend_id: str) -> bool:
        """后端是否可用：注册 id 或 extra_backends 里配置的名称。"""
        backend_id = (backend_id or "").strip().lower()
        if backend_id in list_backends():
            return True
        cfg = self._cfg()
        extras = cfg.get("extra_backends") or {}
        return isinstance(extras, dict) and backend_id in extras
