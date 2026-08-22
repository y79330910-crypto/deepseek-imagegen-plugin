"""Asset HTTP API v2 测试（upload / import / list / get / content / delete）。"""

from __future__ import annotations

import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from imagegen.services.assets import AssetService
from imagegen.services.history import HistoryService

from .api_test_utils import ApiTestServer


def make_png_bytes(width: int = 32, height: int = 32) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (9, 8, 7)).save(buf, format="PNG")
    return buf.getvalue()


def make_multipart(
    data: bytes,
    filename: str = "ref.png",
    content_type: str = "image/png",
    kind: str = "reference",
) -> tuple[bytes, str]:
    boundary = "----imagegen-test-boundary"
    parts = [
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="kind"'
            f'\r\n\r\n{kind}\r\n'
        ).encode("utf-8"),
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            f'filename="{filename}"\r\nContent-Type: {content_type}\r\n\r\n'
        ).encode("utf-8"),
        data,
        b"\r\n",
        f"--{boundary}--\r\n".encode("utf-8"),
    ]
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def insert_generation(db_path: Path, generation_id: str) -> None:
    """插入一条最小 generation 记录（FK 约束要求 generation 先存在）。"""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO generations (id, created_at, output_path, prompt,"
            " warnings_json, metadata_json, request_json)"
            " VALUES (?,?,?,?,?,?,?)",
            (
                generation_id,
                "2026-08-22T00:00:00Z",
                "out.png",
                "p",
                "[]",
                "{}",
                "{}",
            ),
        )
        conn.commit()
    finally:
        conn.close()


class TestAssetApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.asset_svc = AssetService(
            db_path=base / "imagegen.db",
            asset_dir=base / "assets" / "references",
        )
        self.server = ApiTestServer(
            config_path=base / "config.json",
            history_service=HistoryService(base / "imagegen.db"),
            asset_service=self.asset_svc,
        )
        self.addCleanup(self.server.close)

    def test_upload_201_contract(self):
        raw = make_png_bytes()
        body, ctype = make_multipart(raw, filename="miku.png")
        status, _, payload = self.server.json(
            "POST", "/api/v2/assets", body=body, headers={"Content-Type": ctype}
        )
        self.assertEqual(status, 201)
        self.assertEqual(payload["source"], "upload")
        self.assertEqual(payload["kind"], "reference")
        self.assertEqual(payload["original_name"], "miku.png")
        self.assertEqual(payload["mime_type"], "image/png")
        self.assertEqual(payload["size_bytes"], len(raw))
        self.assertEqual(payload["width"], 32)
        self.assertEqual(payload["height"], 32)
        self.assertEqual(payload["content_url"], f"/api/v2/assets/{payload['asset_id']}/content")
        self.assertIn("created_at", payload)
        self.assertNotIn("file_path", payload)
        self.assertNotIn("sha256", payload)
        asset_id = payload["asset_id"]
        status, headers, body = self.server.request(
            "GET", f"/api/v2/assets/{asset_id}/content"
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "image/png")
        self.assertEqual(body, raw)

    def test_upload_missing_file_400(self):
        boundary = "----x"
        body = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="kind"'
            f'\r\n\r\nreference\r\n--{boundary}--\r\n'
        ).encode("utf-8")
        status, _, payload = self.server.json(
            "POST",
            "/api/v2/assets",
            body=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["type"], "validation_error")

    def test_upload_non_image_400(self):
        body, ctype = make_multipart(b"plain text", filename="a.txt", content_type="text/plain")
        status, _, payload = self.server.json(
            "POST", "/api/v2/assets", body=body, headers={"Content-Type": ctype}
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["type"], "validation_error")
        status, _, data = self.server.json("GET", "/api/v2/assets")
        self.assertEqual(data["count"], 0)

    def test_upload_bad_content_type_400(self):
        status, _, payload = self.server.json(
            "POST",
            "/api/v2/assets",
            body=b"{}",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 400)
        self.assertIn("multipart", payload["error"]["message"])

    def test_import_path(self):
        src = Path(self.tmp.name) / "local.png"
        Image.new("RGB", (16, 24), (1, 2, 3)).save(src, format="PNG")
        status, _, payload = self.server.json(
            "POST",
            "/api/v2/assets/import",
            {"path": str(src), "kind": "reference"},
        )
        self.assertEqual(status, 201)
        self.assertEqual(payload["source"], "local")
        self.assertEqual(payload["original_name"], "local.png")
        self.assertEqual(payload["width"], 16)
        self.assertEqual(payload["height"], 24)
        self.assertNotIn("file_path", payload)
        status, _, body = self.server.request(
            "GET", f"/api/v2/assets/{payload['asset_id']}/content"
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, src.read_bytes())

    def test_import_missing_path_404(self):
        status, _, payload = self.server.json(
            "POST",
            "/api/v2/assets/import",
            {"path": str(Path(self.tmp.name) / "nope.png")},
        )
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["type"], "not_found")

    def test_import_requires_path(self):
        status, _, payload = self.server.json("POST", "/api/v2/assets/import", {})
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["type"], "validation_error")

    def test_list_assets(self):
        for i in range(3):
            self.asset_svc.create_from_upload(
                make_png_bytes(), original_name=f"ref-{i}.png"
            )
        status, _, payload = self.server.json("GET", "/api/v2/assets")
        self.assertEqual(status, 200)
        self.assertEqual(payload["count"], 3)
        for item in payload["items"]:
            self.assertNotIn("file_path", item)

    def test_list_search_limit_offset_kind(self):
        for i in range(3):
            self.asset_svc.create_from_upload(
                make_png_bytes(), original_name=f"cat-{i}.png"
            )
        status, _, payload = self.server.json("GET", "/api/v2/assets?q=cat-1")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["items"][0]["original_name"], "cat-1.png")
        status, _, payload = self.server.json("GET", "/api/v2/assets?limit=2&offset=0")
        self.assertEqual(payload["count"], 2)
        status, _, payload = self.server.json("GET", "/api/v2/assets?limit=2&offset=2")
        self.assertEqual(payload["count"], 1)
        status, _, payload = self.server.json("GET", "/api/v2/assets?kind=other")
        self.assertEqual(payload["count"], 0)

    def test_get_asset(self):
        rec = self.asset_svc.create_from_upload(make_png_bytes(), original_name="a.png")
        status, _, payload = self.server.json("GET", f"/api/v2/assets/{rec.asset_id}")
        self.assertEqual(status, 200)
        self.assertEqual(payload["asset_id"], rec.asset_id)
        self.assertEqual(payload["original_name"], "a.png")
        self.assertNotIn("file_path", payload)

    def test_get_unknown_asset_404(self):
        status, _, payload = self.server.json("GET", "/api/v2/assets/deadbeef")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["type"], "not_found")

    def test_content_unknown_asset_404(self):
        status, _, payload = self.server.json("GET", "/api/v2/assets/deadbeef/content")
        self.assertEqual(status, 404)

    def test_delete_unused_asset(self):
        rec = self.asset_svc.create_from_upload(make_png_bytes(), original_name="d.png")
        status, _, payload = self.server.json("DELETE", f"/api/v2/assets/{rec.asset_id}")
        self.assertEqual(status, 200)
        self.assertTrue(payload["deleted"])
        status, _, _ = self.server.json("GET", f"/api/v2/assets/{rec.asset_id}")
        self.assertEqual(status, 404)

    def test_delete_used_asset_409(self):
        rec = self.asset_svc.create_from_upload(make_png_bytes(), original_name="u.png")
        insert_generation(Path(self.tmp.name) / "imagegen.db", "g1")
        self.asset_svc.attach_to_generation("g1", rec.asset_id, "character", 0)
        status, _, payload = self.server.json("DELETE", f"/api/v2/assets/{rec.asset_id}")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["type"], "asset_in_use")

    def test_delete_unknown_asset_404(self):
        status, _, payload = self.server.json("DELETE", "/api/v2/assets/deadbeef")
        self.assertEqual(status, 404)

    def test_wrong_method_405(self):
        status, headers, _ = self.server.json("PATCH", "/api/v2/assets")
        self.assertEqual(status, 405)
        self.assertIn("POST", headers.get("Allow", ""))
