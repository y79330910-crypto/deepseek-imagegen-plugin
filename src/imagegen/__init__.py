"""ImageGen Core：稳定公共 API。

外围客户端（CLI / WebUI / HTTP API）应优先通过本模块与
imagegen.services 消费 Core，而不是直接依赖内部模块布局。
"""

from __future__ import annotations

from .engine import ImageGenEngine
from .errors import (
    BackendError,
    ConfigurationError,
    GenError,
    ImageGenError,
    ValidationError,
)
from .models import GenerateRequest, GenerateResult
from .services import (
    ConfigService,
    DiagnosticService,
    GenerationService,
    ModelService,
)
from ._version import __version__

CORE_API_VERSION = 1

__all__ = [
    "CORE_API_VERSION",
    "ImageGenEngine",
    "GenerateRequest",
    "GenerateResult",
    "ImageGenError",
    "ConfigurationError",
    "BackendError",
    "ValidationError",
    "GenError",
    "GenerationService",
    "ModelService",
    "ConfigService",
    "DiagnosticService",
]
