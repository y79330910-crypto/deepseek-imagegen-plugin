"""Preview 服务：懒生成 WebP 缩略图 + 原子磁盘缓存。

Preview 属于 derived / disposable cache，不是用户数据，不写数据库、不新增配置项。

处理管线：
    source image → cache lookup → Pillow decode → EXIF orientation → resize
    → WebP → atomic disk cache

Cache identity 至少包含：
    logical id + source file size + source mtime_ns + preview profile version

因此：原图未变化 → cache hit；原图变化 / profile 变化 → 自动失效。
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Optional

from ..config import default_preview_cache_dir
from ..errors import ImageGenError


PREVIEW_PROFILE_VERSION = 1
PREVIEW_MAX_SIDE = 512
PREVIEW_QUALITY = 82


class PreviewError(ImageGenError):
    """Preview 生成失败（源文件缺失 / 解码失败等）。"""


def _identity(source: Path) -> str:
    stat = source.stat()
    return f"{stat.st_size}-{stat.st_mtime_ns}"


def _cache_name(logical_id: str, source: Path) -> str:
    return (
        f"{logical_id}__v{PREVIEW_PROFILE_VERSION}__{_identity(source)}.webp"
    )


def create_webp_preview(
    source: Path,
    dst: Path,
    max_side: int = PREVIEW_MAX_SIDE,
    quality: int = PREVIEW_QUALITY,
) -> None:
    """生成 WebP 预览：EXIF orientation → 等比例缩放（不放大）→ 保持透明。

    不裁剪、不拉伸、不修改原图；GIF / animated WebP 取第一帧。
    """
    from PIL import Image, ImageOps

    with Image.open(source) as img:
        img = ImageOps.exif_transpose(img)
        if getattr(img, "is_animated", False):
            img.seek(0)
        img.load()
        mode = img.mode
        if mode == "P" and "transparency" in img.info:
            img = img.convert("RGBA")
        elif mode not in ("RGB", "RGBA", "LA"):
            img = img.convert("RGBA" if mode in ("LA", "PA") else "RGB")
        width, height = img.size
        scale = min(1.0, max_side / max(width, height))
        if scale < 1.0:
            new_size = (
                max(1, round(width * scale)),
                max(1, round(height * scale)),
            )
            img = img.resize(new_size, Image.LANCZOS)
        if img.mode == "LA":
            img = img.convert("RGBA")
        elif img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        img.save(dst, format="WEBP", quality=quality, method=4)


def _remove_prefix(directory: Path, prefix: str) -> None:
    """删除某个 logical id 的全部 preview 缓存（含中断遗留的 tmp 文件）。"""
    if not directory.is_dir():
        return
    for path in directory.glob(f"{prefix}__*"):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


class PreviewService:
    """Generation / Asset 的懒缩略图服务（disk cache）。"""

    def __init__(self, cache_root: Optional[str | Path] = None):
        self.cache_root = (
            Path(cache_root).expanduser()
            if cache_root is not None
            else default_preview_cache_dir()
        )
        self.generations_dir = self.cache_root / "generations"
        self.assets_dir = self.cache_root / "assets"

    def _ensure(self, cache_dir: Path, logical_id: str, source: Path) -> Path:
        source = Path(source)
        if not source.is_file():
            raise PreviewError(f"preview source file missing: {source}")
        cache_file = cache_dir / _cache_name(logical_id, source)
        if cache_file.is_file():
            return cache_file
        cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = cache_dir / (
            f"{logical_id}__v{PREVIEW_PROFILE_VERSION}__"
            f"{_identity(source)}__{uuid.uuid4().hex}.tmp"
        )
        try:
            create_webp_preview(source, tmp)
            os.replace(tmp, cache_file)
        except Exception:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return cache_file

    def generation_thumbnail(
        self, generation_id: str, output_path: str | Path
    ) -> Path:
        """返回 generation 输出图片的 WebP 缩略图缓存路径（懒生成）。"""
        return self._ensure(self.generations_dir, generation_id, Path(output_path))

    def asset_thumbnail(self, asset_id: str, file_path: str | Path) -> Path:
        """返回 managed asset 的 WebP 缩略图缓存路径（懒生成）。"""
        return self._ensure(self.assets_dir, asset_id, Path(file_path))

    def invalidate_generation(self, generation_id: str) -> None:
        """删除 generation preview 缓存（原始输出文件保留）。"""
        _remove_prefix(self.generations_dir, generation_id)

    def invalidate_asset(self, asset_id: str) -> None:
        """删除 asset preview 缓存（配合 managed 文件删除）。"""
        _remove_prefix(self.assets_dir, asset_id)
