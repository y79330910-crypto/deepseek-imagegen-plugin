"""Standalone ImageGen WebUI：静态资源加载（importlib.resources，不依赖源码路径）。"""

from __future__ import annotations

from importlib.resources import files


_STATIC_ROOT = files("imagegen.web").joinpath("static")

ASSET_CONTENT_TYPES = {
    "index.html": "text/html; charset=utf-8",
    "app.js": "application/javascript; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
}

# 只允许这些明确的资源名（防路径穿越 / 任意文件读取）
ALLOWED_ASSETS = ("app.js", "style.css")


def read_index_html() -> str:
    """读取首页 HTML（作为包内资源，不依赖仓库路径）。"""
    return (_STATIC_ROOT / "index.html").read_text(encoding="utf-8")


def read_asset(name: str) -> bytes:
    """读取白名单内的静态资源；未知 / 非法名称抛 FileNotFoundError。"""
    if name not in ALLOWED_ASSETS:
        raise FileNotFoundError(name)
    return (_STATIC_ROOT / name).read_bytes()


def asset_content_type(name: str) -> str:
    return ASSET_CONTENT_TYPES.get(name, "application/octet-stream")
