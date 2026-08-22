"""轻量 multipart/form-data 解析（标准库 email，仅用于文件上传端点）。"""

from __future__ import annotations

from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default as email_default_policy


@dataclass
class MultipartPart:
    name: str
    filename: str
    content_type: str
    data: bytes


def parse_multipart(body: bytes, content_type: str) -> list[MultipartPart]:
    """解析 multipart/form-data 请求体，返回各 part 的字段信息与原始字节。

    非法 Content-Type / 缺少 boundary / 解析失败抛 ValueError（路由映射为 400）。
    """
    ctype = (content_type or "").strip()
    if not ctype.lower().startswith("multipart/form-data"):
        raise ValueError("Content-Type 必须是 multipart/form-data")
    if "boundary=" not in ctype.lower():
        raise ValueError("multipart/form-data 缺少 boundary")
    envelope = (
        f"Content-Type: {ctype}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + body
    )
    message = BytesParser(policy=email_default_policy).parsebytes(envelope)
    parts: list[MultipartPart] = []
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        filename = part.get_filename()
        payload = part.get_payload(decode=True)
        parts.append(
            MultipartPart(
                name=str(name or ""),
                filename=str(filename or ""),
                content_type=part.get_content_type(),
                data=payload if payload is not None else b"",
            )
        )
    return parts
