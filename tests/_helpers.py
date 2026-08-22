"""测试公共工具：假图片字节与 OpenAI-Compatible 上游替身。"""

from __future__ import annotations

import io

from imagegen.openai_client import OpenAIClient


def make_png_bytes(width: int = 64, height: int = 64) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (120, 180, 240)).save(buf, format="PNG")
    return buf.getvalue()


class FakeOpenAIClient(OpenAIClient):
    """OpenAI-Compatible 上游替身：不发起网络请求，返回指定尺寸 PNG。"""

    def __init__(
        self,
        base_url: str = "https://example.com/v1",
        api_key: str = "sk-test",
        size: tuple[int, int] = (1024, 1024),
        text: str = "rewritten prompt",
    ):
        super().__init__(base_url, api_key)
        self.size = size
        self.text = text
        self.requests: list[dict] = []

    def list_models(self) -> list[str]:
        return ["model-a", "model-b"]

    def generate_text(self, messages, model, max_tokens=4096, temperature=0.8, timeout=120) -> str:
        self.requests.append({"kind": "generate_text", "model": model, "messages": messages})
        return self.text

    def generate_image(self, model, prompt, size, quality="", empty_retries=2, retry_delay_base=6.0) -> bytes:
        self.requests.append(
            {
                "kind": "generate_image",
                "model": model,
                "prompt": prompt,
                "size": size,
                "quality": quality,
            }
        )
        return make_png_bytes(*self.size)

    def edit_image(self, model, prompt, size, images, quality="") -> bytes:
        self.requests.append(
            {
                "kind": "edit_image",
                "model": model,
                "prompt": prompt,
                "size": size,
                "quality": quality,
                "images": list(images),
            }
        )
        return make_png_bytes(*self.size)
