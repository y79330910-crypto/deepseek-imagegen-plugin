"""ImageGen Core 公共错误类型（不依赖任何插件结构）。

外围客户端只需依赖 ImageGenError 基类；具体分类供诊断与测试使用。
"""

from __future__ import annotations


class ImageGenError(Exception):
    """ImageGen Core 公共错误基类。"""


class ConfigurationError(ImageGenError):
    """配置 / 依赖环境问题（路径、密钥、依赖缺失）。"""


class UpstreamError(ImageGenError):
    """OpenAI-Compatible 上游服务错误（连接失败、异常响应、空结果）。"""


class HTTPStatusError(UpstreamError):
    """上游返回非 2xx 状态（含 429 重试后）；携带 status 供 fallback 判断。"""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class ValidationError(ImageGenError):
    """输入参数校验失败。"""


class EmptyImageError(UpstreamError):
    """图像接口返回了空结果（常见于上游限流但代理返回 HTTP 200 + 空 data）。"""


class AssetError(ImageGenError):
    """Reference Asset System 公共错误基类。"""


class AssetNotFoundError(AssetError):
    """资产不存在（未知 asset_id / managed 文件丢失 / 本机导入路径找不到）。"""


class AssetInUseError(AssetError):
    """资产已被历史 generation 引用，不允许删除。"""
