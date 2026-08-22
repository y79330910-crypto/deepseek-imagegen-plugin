"""ModelService：按 target（translator / image）拉取上游模型列表。"""

from __future__ import annotations

from typing import Any, Optional

from ..config import load_config
from ..errors import ConfigurationError, ValidationError
from ..openai_client import OpenAIClient


SUPPORTED_TARGETS = ("translator", "image")


class ModelService:
    """两套独立上游的模型发现；拉取失败不阻止手工填写模型。"""

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

    def list_models(self, target: str) -> list[str]:
        """GET {target.base_url}/models，使用该 target 自己的 API Key。"""
        target = (target or "").strip().lower()
        if target not in SUPPORTED_TARGETS:
            raise ValidationError(
                f"未知 target：{target!r}。可选：{' / '.join(SUPPORTED_TARGETS)}"
            )
        info = self._cfg().get(target) or {}
        if not isinstance(info, dict):
            info = {}
        base_url = str(info.get("base_url") or "").strip()
        api_key = str(info.get("api_key") or "").strip()
        if not base_url or not api_key:
            raise ConfigurationError(f"{target} 未配置 base_url / api_key。")
        return OpenAIClient(base_url, api_key).list_models()
