"""GenerationService：生图统一入口（文生图 / 图生图）。"""

from __future__ import annotations

from typing import Any, Optional

from ..engine import ImageGenEngine
from ..models import GenerateRequest, GenerateResult


class GenerationService:
    """包装 ImageGenEngine，对外围客户端提供稳定的生图接口。"""

    def __init__(
        self,
        engine: Optional[ImageGenEngine] = None,
        config: Optional[dict[str, Any]] = None,
        config_service: Optional[Any] = None,
    ):
        """engine / config / config_service 三选一：

        - engine：直接注入；
        - config：注入默认 Engine 的显式配置（不重复解析）；
        - config_service：每次生成前从 ConfigService 刷新配置（保持同一上下文）。
        """
        self._engine = engine or ImageGenEngine(config=config)
        self._config_service = config_service

    def generate(self, request: GenerateRequest) -> GenerateResult:
        """文生图 / 图生图：request.images 非空时自动走编辑流程。"""
        if self._config_service is not None:
            self._engine = ImageGenEngine(config=self._config_service.load())
        return self._engine.generate(request)

    def edit(self, request: GenerateRequest) -> GenerateResult:
        """编辑图片（语义别名：与 generate 共用同一 Engine 流程）。"""
        return self.generate(request)
