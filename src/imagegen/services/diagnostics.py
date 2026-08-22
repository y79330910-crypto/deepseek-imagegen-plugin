"""DiagnosticService：doctor 连通性诊断（提示词 / 图像两套独立上游）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from ..config import default_config_path, load_config
from ..errors import ImageGenError
from ..openai_client import OpenAIClient


class DiagnosticService:
    """统一诊断入口：检查 translator / image 两组 OpenAI-Compatible 连接。"""

    def __init__(
        self,
        config: Optional[dict[str, Any]] = None,
        config_path: Optional[Path] = None,
    ):
        self._config = config
        self._config_path = Path(config_path).expanduser() if config_path else None

    def _cfg(self) -> dict[str, Any]:
        if self._config is not None:
            return self._config
        if self._config_path is not None:
            return load_config(self._config_path)
        return load_config()

    def _check_target(self, target: str, cfg: dict[str, Any]) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "target": target,
            "ok": False,
            "message": "未配置 base_url / api_key",
        }
        info = cfg.get(target) or {}
        if not isinstance(info, dict):
            info = {}
        base_url = str(info.get("base_url") or "").strip()
        api_key = str(info.get("api_key") or "").strip()
        if not base_url or not api_key:
            return entry
        try:
            models = OpenAIClient(base_url, api_key).list_models()
            entry["ok"] = True
            entry["message"] = f"已连接，发现 {len(models)} 个模型"
            entry["model_count"] = len(models)
        except ImageGenError as exc:
            entry["ok"] = False
            entry["message"] = str(exc)
        except Exception as exc:  # noqa: BLE001
            entry["ok"] = False
            entry["message"] = f"{type(exc).__name__}: {exc}"
        return entry

    def doctor(self, size_probe: bool = False, size: str = "") -> dict[str, Any]:
        """运行诊断：检查提示词 / 图像两组上游连通性（size_probe 已废弃，忽略）。"""
        cfg = self._cfg()
        checks = [self._check_target(target, cfg) for target in ("translator", "image")]
        cfg_path = self._config_path or default_config_path()
        return {
            "ok": any(check["ok"] for check in checks),
            "config_file": str(cfg_path),
            "config_exists": cfg_path.exists(),
            "checks": checks,
        }
