"""Backend API v1 与 Registry 测试。"""

from __future__ import annotations

import unittest
from unittest import mock

from imagegen.backends import openai_images
from imagegen.backends import vertex as vertex_mod
from imagegen.backends.base import ImageBackend
from imagegen.backends.openai_images import OpenAIImagesBackend
from imagegen.backends.registry import get_backend, list_backends, register_backend
from imagegen.backends.vertex import VertexBackend

from ._helpers import make_png_bytes


class FakeBackend(ImageBackend):
    id = "test-fake-backend"

    def generate(self, cfg, prompt, width, height, model="", **kwargs):
        return make_png_bytes(width, height)


class TestRegistry(unittest.TestCase):
    def test_default_backends_registered(self):
        names = list_backends()
        self.assertIn("vertex", names)
        self.assertIn("openai-compatible", names)

    def test_get_backend_aliases(self):
        self.assertIsInstance(get_backend(""), VertexBackend)
        self.assertIsInstance(get_backend("vertex"), VertexBackend)
        self.assertIsInstance(get_backend("openai-compatible"), OpenAIImagesBackend)
        self.assertIsInstance(get_backend("extra"), OpenAIImagesBackend)

    def test_get_backend_extra_name_returns_openai_compatible(self):
        backend = get_backend("dragtokens")
        self.assertIsInstance(backend, OpenAIImagesBackend)
        self.assertEqual(backend.name, "dragtokens")

    def test_register_and_get(self):
        fake = FakeBackend()
        register_backend(fake, aliases=["test-fake-alias"])
        self.assertIs(get_backend("test-fake-backend"), fake)
        self.assertIs(get_backend("test-fake-alias"), fake)


class TestVertexBackend(unittest.TestCase):
    def setUp(self):
        self.backend = VertexBackend()

    def test_capabilities(self):
        caps = self.backend.capabilities()
        self.assertTrue(caps.text_to_image)
        self.assertTrue(caps.image_to_image)
        self.assertTrue(caps.multi_reference)
        self.assertFalse(caps.quality)
        self.assertFalse(caps.seed)
        self.assertFalse(caps.exact_size)

    def test_api_version(self):
        self.assertEqual(self.backend.api_version, 1)

    def test_resolve_model_requested_wins(self):
        self.assertEqual(self.backend.resolve_model({}, "my-model"), "my-model")

    def test_resolve_model_auto_from_discovery(self):
        with mock.patch.object(
            vertex_mod, "discover_vertex", return_value={"model": "gemini-3-pro-image"}
        ):
            self.assertEqual(self.backend.resolve_model({}), "gemini-3-pro-image")

    def test_list_models_uses_discovery(self):
        with mock.patch.object(
            vertex_mod,
            "discover_vertex",
            return_value={"image_models": ["gemini-3-pro-image", "gemini-2.5-flash-image"]},
        ):
            self.assertEqual(
                self.backend.list_models({}),
                ["gemini-3-pro-image", "gemini-2.5-flash-image"],
            )

    def test_generate_calls_provider(self):
        with mock.patch.object(vertex_mod, "gen_vertex", return_value=make_png_bytes(32, 32)) as gen:
            data = self.backend.generate({}, "p", 32, 32, "m")
        gen.assert_called_once()
        self.assertEqual(probe_size(data), (32, 32))

    def test_generate_fallback_size_calls_canvas_first(self):
        with mock.patch.object(
            vertex_mod, "gen_vertex_canvas_first", return_value=make_png_bytes(64, 64)
        ) as gen:
            data = self.backend.generate_fallback_size({}, "p", 64, 64, "m")
        gen.assert_called_once()
        self.assertEqual(probe_size(data), (64, 64))

    def test_edit_calls_img2img(self):
        images = [(make_png_bytes(), "image/png", "ref.png")]
        with mock.patch.object(
            vertex_mod, "gen_vertex_img2img", return_value=make_png_bytes(64, 64)
        ) as gen:
            data = self.backend.edit({}, "p", 64, 64, "m", images)
        gen.assert_called_once()
        self.assertEqual(probe_size(data), (64, 64))


class TestOpenAIImagesBackend(unittest.TestCase):
    def setUp(self):
        self.backend = OpenAIImagesBackend("dragtokens")
        self.cfg = {
            "extra_backends": {
                "dragtokens": {
                    "base_url": "https://draw.example.com/v1",
                    "api_key": "sk-test",
                    "model": "gpt-image-2",
                    "models": ["gpt-image-2", "gpt-image-2-4k超分"],
                }
            }
        }

    def test_capabilities(self):
        caps = self.backend.capabilities()
        self.assertTrue(caps.text_to_image)
        self.assertTrue(caps.image_to_image)
        self.assertTrue(caps.quality)
        self.assertTrue(caps.exact_size)

    def test_resolve_model_from_config(self):
        self.assertEqual(self.backend.resolve_model(self.cfg), "gpt-image-2")
        self.assertEqual(
            self.backend.resolve_model(self.cfg, "gpt-image-2-4k超分"),
            "gpt-image-2-4k超分",
        )

    def test_list_models_from_config(self):
        self.assertEqual(
            self.backend.list_models(self.cfg),
            ["gpt-image-2", "gpt-image-2-4k超分"],
        )

    def test_generate_calls_extra_image(self):
        with mock.patch.object(
            openai_images, "gen_extra_image", return_value=make_png_bytes(32, 32)
        ) as gen:
            data = self.backend.generate(
                self.cfg, "p", 1536, 1024, "gpt-image-2",
                size_str="1536x1024", quality="high",
            )
        gen.assert_called_once()
        args = gen.call_args
        self.assertEqual(args.args[1], "dragtokens")
        self.assertEqual(probe_size(data), (32, 32))

    def test_edit_calls_extra_img2img(self):
        images = [(make_png_bytes(), "image/png", "ref.png")]
        with mock.patch.object(
            openai_images, "gen_extra_img2img", return_value=make_png_bytes(64, 64)
        ) as gen:
            data = self.backend.edit(self.cfg, "p", 64, 64, "gpt-image-2", images)
        gen.assert_called_once()
        self.assertEqual(probe_size(data), (64, 64))


def probe_size(data: bytes):
    from imagegen.image_utils import probe_image_size

    return probe_image_size(data)
