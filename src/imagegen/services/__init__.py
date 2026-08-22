"""Application Service 层：外围客户端（CLI / WebUI / 未来 HTTP API）的稳定入口。"""

from __future__ import annotations

from .assets import AssetLink, AssetRecord, AssetService
from .config import ConfigService
from .diagnostics import DiagnosticService
from .generation import GenerationService
from .history import HistoryRecord, HistoryService
from .models import ModelService
from .previews import PreviewService
from .references import ReferenceResolver

__all__ = [
    "AssetLink",
    "AssetRecord",
    "AssetService",
    "ConfigService",
    "DiagnosticService",
    "GenerationService",
    "HistoryRecord",
    "HistoryService",
    "ModelService",
    "PreviewService",
    "ReferenceResolver",
]
