"""ImageGen Core 通用错误类型（不依赖任何插件结构）。"""

from __future__ import annotations


class GenError(Exception):
    """可向用户展示的中文错误。"""


class EmptyImageError(GenError):
    """图像接口返回了空结果（常见于上游限流但代理返回 HTTP 200 + 空 data）。"""
