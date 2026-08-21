"""HTTP JSON / 错误响应辅助（统一错误契约）。"""

from __future__ import annotations

import json
from typing import Any


class ApiError(Exception):
    """HTTP 层内部错误：路由 / 输入解析等，统一映射为错误契约。"""

    def __init__(self, status: int, error_type: str, message: str):
        super().__init__(message)
        self.status = status
        self.error_type = error_type
        self.message = message


def parse_json_payload(body: bytes) -> dict[str, Any]:
    """解析请求体；空 body / 非法 JSON / 非对象 → ApiError(400, invalid_json)。"""
    if not body or not body.strip():
        raise ApiError(400, "invalid_json", "request body must be valid JSON")
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiError(400, "invalid_json", "invalid JSON body") from exc
    if not isinstance(data, dict):
        raise ApiError(400, "invalid_json", "request body must be a JSON object")
    return data
