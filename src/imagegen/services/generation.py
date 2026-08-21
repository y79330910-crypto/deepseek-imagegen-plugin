"""GenerationService：生图统一入口（文生图 / 图生图）。"""

from __future__ import annotations

from typing import Optional

from ..engine import ImageGenEngine
from ..models import GenerateRequest, GenerateResult


class GenerationService:
    """包装 ImageGenEngine，对外围客户端提供稳定的生图接口。"""

    def __init__(self, engine: Optional[ImageGenEngine] = None):
        self._engine = engine or ImageGenEngine()

    def generate(self, request: GenerateRequest) -> GenerateResult:
        """文生图 / 图生图：request.images 非空时自动走编辑流程。"""
        return self._engine.generate(request)

    def edit(self, request: GenerateRequest) -> GenerateResult:
        """编辑图片（语义别名：与 generate 共用同一 Engine 流程）。"""
        return self.generate(request)
