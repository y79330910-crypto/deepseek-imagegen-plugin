"""图片与文件工具：尺寸解析/探测、画布兜底、参考图、输出路径与镜像副本。"""

from __future__ import annotations

import base64
import io
import os
import random
import re
import shutil
import time
import urllib.parse
from pathlib import Path
from typing import Any, Optional

from .config import APP_NAME
from .errors import GenError, ValidationError
from .http import BROWSER_UA, HEALTH_TIMEOUT, http


MAX_INIT_BYTES = 20 * 1024 * 1024


def parse_size(size: str) -> tuple[int, int]:
    """解析 WxH 尺寸，支持 x/X/×。"""
    match = re.fullmatch(r"\s*(\d{2,5})\s*[xX×]\s*(\d{2,5})\s*", size or "")
    if not match:
        raise ValidationError(f"无效的尺寸格式：{size!r}，应为 WxH，例如 1024x1024")
    width, height = int(match.group(1)), int(match.group(2))
    if width < 16 or height < 16 or width > 4096 or height > 4096:
        raise ValidationError(f"尺寸超出支持范围（16~4096）：{width}x{height}")
    return width, height


def slugify(text: str, max_len: int = 50) -> str:
    slug = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", text, flags=re.UNICODE).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return (slug[:max_len] or "image").strip("-") or "image"


def default_output_path(prompt: str, seed: int, cfg: dict[str, Any], ext: str = "png") -> Path:
    """默认输出路径：save_dir 生效（未配置时输出到当前目录）。"""
    save_dir = (cfg.get("save_dir") or "").strip()
    base = Path(save_dir).expanduser() if save_dir else Path.cwd()
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError:
        base = Path.cwd()
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    filename = f"{APP_NAME}_{timestamp}_{slugify(prompt)}_{seed}.{ext}"
    return base / filename


def mirror_output(path: str, cfg: dict[str, Any]) -> Optional[str]:
    """把生成结果复制一份到 mirror_dir；失败不影响主输出。"""
    mirror_dir = str(cfg.get("mirror_dir") or "").strip()
    if not mirror_dir:
        return None
    try:
        mirror_dir_path = Path(mirror_dir).expanduser()
        mirror_dir_path.mkdir(parents=True, exist_ok=True)
        dest = mirror_dir_path / Path(path).name
        shutil.copy2(path, dest)
        return str(dest)
    except Exception:  # noqa: BLE001
        return None


def ext_from_content_type(content_type: str) -> str:
    ctype = content_type.lower().split(";")[0].strip()
    if ctype in ("image/jpeg", "image/jpg"):
        return "jpg"
    if ctype == "image/webp":
        return "webp"
    return "png"


def io_bytes(data: bytes):
    return io.BytesIO(data)


def _guess_mime(data: bytes, name: str = "") -> str:
    """按文件头嗅探图片 MIME，嗅探不到时按扩展名兜底。"""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    ext = Path(name).suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(ext, "application/octet-stream")


def probe_image_size(data: bytes, mime: str = "") -> Optional[tuple[int, int]]:
    """不依赖第三方库，从图片文件头解析宽高（PNG / JPEG / WebP）。"""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
            width = int.from_bytes(data[16:20], "big")
            height = int.from_bytes(data[20:24], "big")
            if 1 <= width <= 32768 and 1 <= height <= 32768:
                return width, height
        if data[:3] == b"\xff\xd8\xff" and len(data) >= 12:
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                if i + 4 > len(data):
                    break
                seg_len = int.from_bytes(data[i + 2 : i + 4], "big")
                if (
                    marker
                    in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF)
                    and i + 9 <= len(data)
                ):
                    height = int.from_bytes(data[i + 5 : i + 7], "big")
                    width = int.from_bytes(data[i + 7 : i + 9], "big")
                    if 1 <= width <= 32768 and 1 <= height <= 32768:
                        return width, height
                i += 2 + seg_len
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) >= 30:
            if data[12:16] == b"VP8X":
                width = int.from_bytes(data[24:27], "little") + 1
                height = int.from_bytes(data[27:30], "little") + 1
                return width, height
            if data[12:16] == b"VP8 ":
                width = int.from_bytes(data[26:28], "little") & 0x3FFF
                height = int.from_bytes(data[28:30], "little") & 0x3FFF
                return width, height
            if data[12:16] == b"VP8L" and len(data) >= 25:
                bits = int.from_bytes(data[21:25], "little")
                width = (bits & 0x3FFF) + 1
                height = ((bits >> 14) & 0x3FFF) + 1
                return width, height
    except Exception:  # noqa: BLE001
        return None
    return None


def probe_image_size_ext(data: bytes, mime: str = "") -> Optional[tuple[int, int]]:
    """优先文件头解析；失败时尝试 Pillow（可选依赖）。"""
    size = probe_image_size(data, mime)
    if size:
        return size
    try:
        from PIL import Image  # type: ignore  # noqa: PLC0415

        with Image.open(io_bytes(data)) as img:
            return img.size
    except Exception:  # noqa: BLE001
        return None


def aspect_ratio_key(width: int, height: int) -> tuple[int, int]:
    """把宽高比归一到常见画幅档位，返回 (w, h)。"""
    if width <= 0 or height <= 0:
        return (1, 1)
    common = [
        (1, 1), (2, 3), (3, 4), (9, 16), (3, 2), (4, 3), (16, 9), (21, 9), (1, 2), (2, 1),
    ]
    ratio = width / height
    best, best_err = common[0], abs(ratio - common[0][0] / common[0][1])
    for cand in common[1:]:
        err = abs(ratio - cand[0] / cand[1])
        if err < best_err:
            best, best_err = cand, err
    return best


def aspect_ratio_text(width: int, height: int) -> str:
    w, h = aspect_ratio_key(width, height)
    return f"{w}:{h}"


def sizes_match(
    requested: tuple[int, int],
    actual: Optional[tuple[int, int]],
    tolerance: float = 0.06,
) -> dict[str, Any]:
    """判断生成图真实尺寸是否满足要求（像素一致 / 画幅档位一致 / 比例误差可接受）。"""
    result: dict[str, Any] = {"ok": False, "reason": ""}
    if not actual:
        result["reason"] = "无法读取生成图的真实尺寸"
        return result
    rw, rh = requested
    aw, ah = actual
    if (rw, rh) == (aw, ah):
        result["ok"] = True
        result["reason"] = "像素尺寸完全一致"
        return result
    r_kind = aspect_ratio_key(rw, rh)
    a_kind = aspect_ratio_key(aw, ah)
    if (rh > rw) != (ah > aw):
        result["reason"] = (
            f"方向不符：要求{'竖版' if rh > rw else '横版'}，"
            f"实际{'竖版' if ah > aw else '横版'}"
        )
        return result
    if r_kind == a_kind:
        result["ok"] = True
        result["reason"] = f"画幅一致（{r_kind[0]}:{r_kind[1]}），分辨率由后端决定"
        return result
    r_ratio = rw / rh
    a_ratio = aw / ah
    if abs(r_ratio - a_ratio) / max(r_ratio, a_ratio) <= max(tolerance, 0.01):
        result["ok"] = True
        result["reason"] = (
            f"画幅接近（误差 {abs(r_ratio - a_ratio) / max(r_ratio, a_ratio) * 100:.1f}%）"
        )
        return result
    result["reason"] = (
        f"画幅不符：要求 {rw}x{rh}（{r_kind[0]}:{r_kind[1]}），"
        f"实际 {aw}x{ah}（{a_kind[0]}:{a_kind[1]}）"
    )
    return result


def load_init_image(ref: str) -> tuple[bytes, str, str]:
    """读取参考图，返回 (字节, MIME, 建议文件名)。支持本地路径 / http(s) / data URI。"""
    ref = (ref or "").strip().strip('"')
    if not ref:
        raise GenError("--image 不能为空。")
    name = "reference"
    data: bytes = b""
    mime = ""
    if ref.startswith(("http://", "https://")):
        status, body, ctype = http(
            ref, headers={"User-Agent": BROWSER_UA}, timeout=HEALTH_TIMEOUT * 3
        )
        if not ctype.lower().startswith("image/"):
            raise GenError(f"--image 指向的内容不是图片（Content-Type: {ctype}）。")
        data = body
        parsed_name = urllib.parse.urlparse(ref).path.rsplit("/", 1)[-1]
        if parsed_name:
            name = parsed_name
        mime = ctype.split(";")[0].strip()
    elif ref.startswith("data:image/"):
        header, _, b64 = ref.partition(",")
        try:
            data = base64.b64decode(b64)
        except Exception as exc:  # noqa: BLE001
            raise GenError(f"data URI 图片解码失败：{exc}") from exc
        mime = header[5:].split(";")[0].strip()
    else:
        path = Path(ref).expanduser()
        if not path.is_file():
            raise GenError(f"找不到图片文件：{path}")
        data = path.read_bytes()
        name = path.name
    if not data:
        raise GenError("图片内容为空。")
    if len(data) > MAX_INIT_BYTES:
        raise GenError(
            f"图片过大（{len(data) // 1024} KB），上限 {MAX_INIT_BYTES // (1024 * 1024)} MB。"
        )
    mime = mime or _guess_mime(data, name)
    return data, mime, name


def multipart(
    fields: dict[str, Any], files: list[tuple[str, str, bytes, str]]
) -> tuple[bytes, str]:
    """构造 multipart/form-data 请求体，返回 (body, Content-Type)。"""
    boundary = "----imagegen" + "".join(random.choices("abcdef0123456789", k=16))
    parts: list[bytes] = []
    for field_name, value in fields.items():
        parts.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{field_name}"'
                f"\r\n\r\n{value}\r\n"
            ).encode("utf-8")
        )
    for field_name, filename, content, ctype in files:
        safe_name = filename.replace('"', "")
        parts.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{field_name}"; '
                f'filename="{safe_name}"\r\nContent-Type: {ctype}\r\n\r\n'
            ).encode("utf-8")
        )
        parts.append(content)
        parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"
