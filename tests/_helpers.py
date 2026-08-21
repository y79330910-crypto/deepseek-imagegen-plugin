"""测试公共工具：假图片字节与测试用后端替身。"""

from __future__ import annotations

import io

from imagegen.backends.base import BackendCapabilities


def make_png_bytes(width: int = 64, height: int = 64) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (120, 180, 240)).save(buf, format="PNG")
    return buf.getvalue()


class FakeVertexBackend:
    """Vertex 后端替身：返回指定尺寸 PNG，不做任何网络请求。"""

    id = "vertex"

    def capabilities(self):
        return BackendCapabilities(
            text_to_image=True,
            image_to_image=True,
            multi_reference=True,
        )

    def resolve_model(self, cfg, requested=""):
        return (requested or "").strip() or "gemini-3-pro-image"

    def generate(self, cfg, prompt, width, height, model="", **kwargs):
        return make_png_bytes(width, height)

    def generate_fallback_size(self, cfg, prompt, width, height, model="", **kwargs):
        return make_png_bytes(width, height)

    def edit(self, cfg, prompt, width, height, model, images, **kwargs):
        return make_png_bytes(width, height)


class FakeOpenAIBackend:
    """OpenAI 兼容后端替身（用于验证 Engine 的备用后端编排）。"""

    id = "openai-compatible"

    def __init__(self, name: str = "dragtokens"):
        self.name = name

    def capabilities(self):
        return BackendCapabilities(
            text_to_image=True,
            image_to_image=True,
            multi_reference=True,
            quality=True,
        )

    def resolve_model(self, cfg, requested=""):
        requested = (requested or "").strip()
        if requested:
            return requested
        info = (cfg.get("extra_backends") or {}).get(self.name) or {}
        return str(info.get("model") or "gpt-image-2")

    def generate(self, cfg, prompt, width, height, model="", **kwargs):
        return make_png_bytes(width, height)

    def edit(self, cfg, prompt, width, height, model, images, **kwargs):
        return make_png_bytes(width, height)
