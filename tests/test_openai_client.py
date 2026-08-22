"""统一 OpenAI-Compatible Client 测试：endpoint / 文本 fallback / 图像 / 模型。"""

from __future__ import annotations

import base64
import json
import unittest
from unittest import mock

from imagegen.errors import ConfigurationError, HTTPStatusError
from imagegen.openai_client import (
    OpenAIClient,
    endpoint,
    normalize_base_url,
)


def png_json(b64_field: str = "b64_json") -> bytes:
    raw = b"fake-image-bytes"
    return json.dumps(
        {"data": [{b64_field: base64.b64encode(raw).decode("ascii")}]}
    ).encode("utf-8")


class TestEndpointNormalization(unittest.TestCase):
    def test_all_base_url_forms_produce_correct_endpoints(self):
        bases = (
            "https://example.com",
            "https://example.com/",
            "https://example.com/v1",
            "https://example.com/v1/",
        )
        for base in bases:
            self.assertEqual(
                endpoint(base, "models"), "https://example.com/v1/models", base
            )
            self.assertEqual(
                endpoint(base, "chat/completions"),
                "https://example.com/v1/chat/completions",
                base,
            )
            self.assertEqual(
                endpoint(base, "responses"),
                "https://example.com/v1/responses",
                base,
            )
            self.assertEqual(
                endpoint(base, "images/generations"),
                "https://example.com/v1/images/generations",
                base,
            )
            self.assertEqual(
                endpoint(base, "images/edits"),
                "https://example.com/v1/images/edits",
                base,
            )
            self.assertNotIn("/v1/v1/", endpoint(base, "models"))

    def test_normalize_keeps_existing_v1(self):
        self.assertEqual(normalize_base_url("https://example.com/v1/"), "https://example.com/v1")
        self.assertEqual(normalize_base_url("https://example.com"), "https://example.com/v1")

    def test_empty_base_url_rejected(self):
        with self.assertRaises(ConfigurationError):
            normalize_base_url("")


class TestListModels(unittest.TestCase):
    def test_parses_object_and_string_models(self):
        body = json.dumps({"data": [{"id": "a"}, "b", {"id": "c"}]}).encode("utf-8")
        with mock.patch(
            "imagegen.openai_client.http",
            return_value=(200, body, "application/json"),
        ) as http_mock:
            models = OpenAIClient("https://example.com", "sk-1").list_models()
        self.assertEqual(models, ["a", "b", "c"])
        self.assertIn("https://example.com/v1/models", http_mock.call_args[0][0])
        self.assertIn("Bearer sk-1", http_mock.call_args[1]["headers"]["Authorization"])


class TestChatCompletions(unittest.TestCase):
    def setUp(self):
        self.client = OpenAIClient("https://example.com/v1", "sk-1")
        self.messages = [{"role": "user", "content": "hi"}]

    def test_chat_success(self):
        body = json.dumps(
            {"choices": [{"message": {"content": "hello world"}}]}
        ).encode("utf-8")
        with mock.patch(
            "imagegen.openai_client.http",
            return_value=(200, body, "application/json"),
        ) as http_mock:
            text = self.client.generate_text(self.messages, "model-a")
        self.assertEqual(text, "hello world")
        url = http_mock.call_args[0][0]
        self.assertEqual(url, "https://example.com/v1/chat/completions")

    def _responses_body(self) -> bytes:
        return json.dumps(
            {
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "fallback text"}],
                    }
                ]
            }
        ).encode("utf-8")

    def test_chat_404_405_501_fall_back_to_responses(self):
        for status in (404, 405, 501):
            with self.subTest(status=status):
                http_mock = mock.Mock(
                    side_effect=[
                        HTTPStatusError(status, f"http {status}"),
                        (200, self._responses_body(), "application/json"),
                    ]
                )
                with mock.patch("imagegen.openai_client.http", http_mock):
                    text = self.client.generate_text(self.messages, "model-a")
                self.assertEqual(text, "fallback text")
                self.assertEqual(http_mock.call_count, 2)
                urls = [call[0][0] for call in http_mock.call_args_list]
                self.assertEqual(
                    urls,
                    [
                        "https://example.com/v1/chat/completions",
                        "https://example.com/v1/responses",
                    ],
                )

    def test_chat_forbidden_statuses_do_not_fallback(self):
        for status in (401, 403, 429, 500):
            with self.subTest(status=status):
                http_mock = mock.Mock(side_effect=HTTPStatusError(status, f"http {status}"))
                with mock.patch("imagegen.openai_client.http", http_mock):
                    with self.assertRaises(HTTPStatusError) as ctx:
                        self.client.generate_text(self.messages, "model-a")
                self.assertEqual(ctx.exception.status, status)
                self.assertEqual(http_mock.call_count, 1)

    def test_empty_model_rejected(self):
        with mock.patch("imagegen.openai_client.http") as http_mock:
            with self.assertRaises(ConfigurationError):
                self.client.generate_text(self.messages, "")
        http_mock.assert_not_called()


class TestGenerateImage(unittest.TestCase):
    def setUp(self):
        self.client = OpenAIClient("https://example.com/v1", "sk-1")

    def test_b64_json_and_image_field(self):
        for field in ("b64_json", "image"):
            with self.subTest(field=field):
                with mock.patch(
                    "imagegen.openai_client.http",
                    return_value=(200, png_json(field), "application/json"),
                ):
                    data = self.client.generate_image("img-m", "a cat", "1920x1080")
                self.assertEqual(data, b"fake-image-bytes")

    def test_url_response_fetched(self):
        body = json.dumps({"data": [{"url": "https://cdn.example.com/x.png"}]}).encode("utf-8")
        http_mock = mock.Mock(
            side_effect=[
                (200, body, "application/json"),
                (200, b"url-image-bytes", "image/png"),
            ]
        )
        with mock.patch("imagegen.openai_client.http", http_mock):
            data = self.client.generate_image("img-m", "a cat", "1080x1920")
        self.assertEqual(data, b"url-image-bytes")
        self.assertEqual(http_mock.call_count, 2)

    def test_data_url_response(self):
        raw = b"data-image-bytes"
        b64 = base64.b64encode(raw).decode("ascii")
        body = json.dumps({"data": [{"url": f"data:image/png;base64,{b64}"}]}).encode("utf-8")
        with mock.patch(
            "imagegen.openai_client.http",
            return_value=(200, body, "application/json"),
        ):
            data = self.client.generate_image("img-m", "a cat", "1024x1024")
        self.assertEqual(data, raw)

    def test_payload_size_verbatim_quality_omitted(self):
        def fake_http(url, **kwargs):
            payload = kwargs.get("payload")
            self.assertEqual(payload["size"], "3440x1440")
            self.assertNotIn("quality", payload)
            self.assertEqual(url, "https://example.com/v1/images/generations")
            return 200, png_json(), "application/json"

        with mock.patch("imagegen.openai_client.http", side_effect=fake_http):
            self.client.generate_image("img-m", "a cat", "3440x1440", quality="")

    def test_quality_included_when_set(self):
        def fake_http(url, **kwargs):
            self.assertEqual(kwargs["payload"]["quality"], "high")
            return 200, png_json(), "application/json"

        with mock.patch("imagegen.openai_client.http", side_effect=fake_http):
            self.client.generate_image("img-m", "a cat", "1024x1024", quality="high")


class TestEditImage(unittest.TestCase):
    def test_multipart_multiple_images(self):
        client = OpenAIClient("https://example.com", "sk-2")
        images = [
            (b"png-a", "image/png", "a.png"),
            (b"png-b", "image/png", "b.png"),
        ]
        captured: dict = {}

        def fake_http(url, **kwargs):
            captured["url"] = url
            captured["headers"] = kwargs.get("headers") or {}
            captured["raw_body"] = kwargs.get("raw_body") or b""
            captured["auth"] = captured["headers"].get("Authorization", "")
            return 200, png_json(), "application/json"

        with mock.patch("imagegen.openai_client.http", side_effect=fake_http):
            client.edit_image("img-m", "change it", "1232x1824", images)
        self.assertEqual(captured["url"], "https://example.com/v1/images/edits")
        self.assertEqual(captured["auth"], "Bearer sk-2")
        raw = captured["raw_body"].decode("utf-8", errors="replace")
        self.assertIn('name="model"', raw)
        self.assertIn("img-m", raw)
        self.assertIn('name="size"', raw)
        self.assertIn("1232x1824", raw)
        self.assertIn('filename="a.png"', raw)
        self.assertIn('filename="b.png"', raw)
        self.assertIn("png-a", raw)
        self.assertIn("png-b", raw)
        self.assertNotIn("quality", raw)

    def test_quality_field_included_when_set(self):
        client = OpenAIClient("https://example.com", "sk-2")

        def fake_http(url, **kwargs):
            raw = (kwargs.get("raw_body") or b"").decode("utf-8", errors="replace")
            self.assertIn('name="quality"', raw)
            self.assertIn("medium", raw)
            return 200, png_json(), "application/json"

        with mock.patch("imagegen.openai_client.http", side_effect=fake_http):
            client.edit_image("img-m", "x", "1024x1024", [(b"a", "image/png", "a.png")], quality="medium")
