"""统一 OpenAI-Compatible Client：提示词 / 图像 / 模型拉取共用同一实现。

架构：只保留两套独立连接（translator 与 image），各自有 base_url / api_key / model，
共用本 Client。所有 URL 统一经 endpoint normalization 构造，禁止模块各自拼 URL。
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any, Optional

from .errors import (
    ConfigurationError,
    EmptyImageError,
    HTTPStatusError,
    UpstreamError,
)
from .http import HEALTH_TIMEOUT, http
from .image_utils import multipart
from ._version import __version__


# Chat Completions 明确不支持（端点不存在 / 方法不允许 / 未实现）时才 fallback Responses
FALLBACK_STATUSES = (404, 405, 501)


def normalize_base_url(base_url: str) -> str:
    """规范化 base_url：自动补 /v1，去掉尾部斜杠，禁止出现 /v1/v1。"""
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise ConfigurationError("base_url 不能为空。")
    if base.endswith("/v1"):
        return base
    return base + "/v1"


def endpoint(base_url: str, path: str) -> str:
    """构造 OpenAI-Compatible 端点：normalize 后的 base + /v1 + path。"""
    return normalize_base_url(base_url) + "/" + str(path).lstrip("/")


def extract_image_from_response(body: bytes) -> bytes:
    """从 OpenAI 兼容图像接口 JSON 中提取第一张图片字节（b64_json / image / url / data URL）。"""
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpstreamError(f"图像接口返回了无法解析的内容：{body[:300]!r}") from exc
    if isinstance(data, dict) and data.get("error"):
        err = data["error"]
        raise UpstreamError(f"图像接口返回错误：{err.get('message') or err}")
    items = (data.get("data") or []) if isinstance(data, dict) else []
    if not items:
        raise EmptyImageError(
            "上游暂时没有返回图片（接口返回空列表，通常是限流或代理吞掉了错误）。"
        )
    item = items[0]
    b64 = item.get("b64_json") or item.get("image")
    if b64:
        try:
            return base64.b64decode(b64)
        except Exception as exc:  # noqa: BLE001
            raise UpstreamError(f"图片 base64 解码失败：{exc}") from exc
    url = item.get("url")
    if url:
        if url.startswith("data:image/"):
            _, b64 = url.split(",", 1)
            return base64.b64decode(b64)
        _status, body, _ctype = http(url)
        return body
    raise UpstreamError(f"图像接口返回中缺少图片数据：{body[:500]!r}")


def _chat_completions_text(body: bytes) -> str:
    data = json.loads(body.decode("utf-8", errors="replace"))
    try:
        content = data["choices"][0]["message"].get("content")
    except (KeyError, IndexError) as exc:
        raise UpstreamError(f"聊天接口返回异常：{str(data)[:300]}") from exc
    return str(content or "").strip()


def _responses_text(body: bytes) -> str:
    data = json.loads(body.decode("utf-8", errors="replace"))
    parts: list[str] = []
    for item in data.get("output", []):
        if item.get("type") == "message":
            for chunk in item.get("content", []):
                if chunk.get("type") == "output_text" and chunk.get("text"):
                    parts.append(str(chunk["text"]))
    if not parts:
        raise UpstreamError(f"Responses 接口返回异常：{str(data)[:300]}")
    return "\n".join(parts).strip()


class OpenAIClient:
    """单个 OpenAI-Compatible 上游的薄客户端（文本 / 图像 / 模型）。"""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = normalize_base_url(base_url)
        self.api_key = (api_key or "").strip()

    def _headers(self, json_body: bool = False) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": f"ImageGen/{__version__}",
        }
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def list_models(self) -> list[str]:
        """GET /v1/models；失败抛错（模型拉取不是强依赖，调用方可降级为手填）。"""
        _status, body, _ctype = http(
            endpoint(self.base_url, "models"),
            method="GET",
            headers=self._headers(),
            timeout=HEALTH_TIMEOUT,
        )
        data = json.loads(body.decode("utf-8", errors="replace"))
        raw = data.get("data", []) if isinstance(data, dict) else []
        out: list[str] = []
        for item in raw:
            model_id = item if isinstance(item, str) else (
                item.get("id") if isinstance(item, dict) else ""
            )
            if model_id:
                out.append(str(model_id))
        return out

    def generate_text(
        self,
        messages: list[dict[str, Any]],
        model: str,
        max_tokens: int = 4096,
        temperature: float = 0.8,
        timeout: int = 120,
    ) -> str:
        """默认 POST /v1/chat/completions；仅 404/405/501 时 fallback /v1/responses。"""
        model = (model or "").strip()
        if not model:
            raise ConfigurationError("提示词模型未配置（translator.model）。")
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        try:
            _status, body, _ctype = http(
                endpoint(self.base_url, "chat/completions"),
                method="POST",
                headers=self._headers(json_body=True),
                payload=payload,
                timeout=timeout,
            )
        except HTTPStatusError as exc:
            if exc.status not in FALLBACK_STATUSES:
                raise
            return self._responses(messages, model, max_tokens, timeout)
        return _chat_completions_text(body)

    def _responses(
        self,
        messages: list[dict[str, Any]],
        model: str,
        max_tokens: int,
        timeout: int,
    ) -> str:
        system = "\n\n".join(
            str(m.get("content") or "")
            for m in messages
            if m.get("role") == "system"
        )
        user = "\n\n".join(
            str(m.get("content") or "")
            for m in messages
            if m.get("role") == "user"
        )
        payload = {
            "model": model,
            "instructions": system,
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": user}],
                }
            ],
            "max_output_tokens": max_tokens,
        }
        _status, body, _ctype = http(
            endpoint(self.base_url, "responses"),
            method="POST",
            headers=self._headers(json_body=True),
            payload=payload,
            timeout=timeout,
        )
        return _responses_text(body)

    def generate_image(
        self,
        model: str,
        prompt: str,
        size: str,
        quality: str = "",
        empty_retries: int = 2,
        retry_delay_base: float = 6.0,
    ) -> bytes:
        """POST /v1/images/generations 文生图；quality 为空时不发送该字段。"""
        model = (model or "").strip()
        if not model:
            raise ConfigurationError("图像模型未配置（image.model）。")
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "n": 1,
            "size": size,
        }
        if quality:
            payload["quality"] = quality
        last_err = ""
        for attempt in range(max(1, empty_retries + 1)):
            try:
                _status, body, _ctype = http(
                    endpoint(self.base_url, "images/generations"),
                    method="POST",
                    headers=self._headers(json_body=True),
                    payload=payload,
                )
                return extract_image_from_response(body)
            except EmptyImageError as exc:
                last_err = str(exc)
                if attempt < empty_retries:
                    time.sleep(retry_delay_base * (2**attempt))
                    continue
                raise
        raise UpstreamError(last_err or "图像接口未返回图片")

    def edit_image(
        self,
        model: str,
        prompt: str,
        size: str,
        images: list[tuple[bytes, str, str]],
        quality: str = "",
    ) -> bytes:
        """POST /v1/images/edits 图生图 / 参考图；支持多图。"""
        model = (model or "").strip()
        if not model:
            raise ConfigurationError("图像模型未配置（image.model）。")
        fields: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "n": "1",
            "size": size,
        }
        if quality:
            fields["quality"] = quality
        files = [("image", name, data, mime) for (data, mime, name) in images]
        body, content_type = multipart(fields, files)
        _status, resp_body, _ctype = http(
            endpoint(self.base_url, "images/edits"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": content_type,
                "User-Agent": f"ImageGen/{__version__}",
            },
            raw_body=body,
        )
        return extract_image_from_response(resp_body)
