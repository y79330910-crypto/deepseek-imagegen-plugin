"""通用 HTTP 工具：超时、429 退避、空结果重试（代码默认值）。"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Optional

from .errors import GenError, HTTPStatusError


# 高清图（如 2K/4K）生成耗时较长，默认超时放宽到 600 秒
DEFAULT_TIMEOUT = 600
HEALTH_TIMEOUT = 8
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def http(
    url: str,
    *,
    method: str = "GET",
    headers: Optional[dict[str, str]] = None,
    payload: Optional[dict[str, Any]] = None,
    raw_body: Optional[bytes] = None,
    timeout: int = DEFAULT_TIMEOUT,
    retry_delay_base: float = 6.0,
) -> tuple[int, bytes, str]:
    """发起 HTTP 请求，返回 (状态码, 响应字节, Content-Type)。429 自动退避重试。"""
    data = None
    if raw_body is not None:
        data = raw_body
    elif payload is not None:
        data = json.dumps(payload).encode("utf-8")
    last_detail = ""
    for attempt in range(3):
        req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read(), resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            body = b""
            try:
                body = exc.read()
            except Exception:  # noqa: BLE001
                pass
            detail = body[:800].decode("utf-8", errors="replace")
            if exc.code == 429 and attempt < 2:
                delay = retry_delay_base * (2**attempt)
                time.sleep(delay)
                last_detail = detail
                continue
            if exc.code == 429:
                raise HTTPStatusError(
                    429, f"上游接口限流（429），重试后仍失败：{detail[:200]}"
                ) from exc
            raise HTTPStatusError(
                exc.code, f"接口返回 HTTP {exc.code}：{detail[:300]}"
            ) from exc
        except urllib.error.URLError as exc:
            raise GenError(f"无法连接 {url}：{exc.reason}") from exc
        except TimeoutError as exc:
            raise GenError(f"请求超时（{timeout} 秒）：{url}") from exc
    raise HTTPStatusError(
        429, f"上游接口持续限流（429），请稍后再试：{last_detail[:200]}"
    )
