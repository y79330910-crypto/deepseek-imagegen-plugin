"""AssetService 单元测试（临时 DB / 临时 asset 目录，不触碰真实用户数据）。"""

from __future__ import annotations

import hashlib
import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from imagegen.errors import AssetInUseError, AssetNotFoundError, ValidationError
from imagegen.services.assets import AssetService


def make_png_bytes(width: int = 64, height: int = 48) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


class TestAssetService(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "imagegen.db"
        self.asset_dir = Path(self.tmp.name) / "assets" / "references"
        self.svc = AssetService(db_path=self.db_path, asset_dir=self.asset_dir)

    def test_create_from_upload(self):
        data = make_png_bytes()
        rec = self.svc.create_from_upload(
            data, original_name="miku.png", mime_type="image/png", metadata={"tag": "x"}
        )
        self.assertEqual(rec.source, "upload")
        self.assertEqual(rec.kind, "reference")
        self.assertEqual(rec.original_name, "miku.png")
        self.assertEqual(rec.mime_type, "image/png")
        self.assertEqual(rec.size_bytes, len(data))
        self.assertEqual((rec.width, rec.height), (64, 48))
        self.assertEqual(rec.sha256, hashlib.sha256(data).hexdigest())
        self.assertEqual(rec.metadata, {"tag": "x"})
        file_path = Path(rec.file_path)
        self.assertTrue(file_path.is_file())
        self.assertEqual(file_path.read_bytes(), data)
        self.assertEqual(file_path.name, f"{rec.asset_id}.png")
        self.assertTrue(str(file_path).startswith(str(self.asset_dir)))

    def test_create_from_upload_without_mime_sniffs(self):
        data = make_png_bytes()
        rec = self.svc.create_from_upload(data, original_name="unknown.bin")
        self.assertEqual(rec.mime_type, "image/png")
        self.assertEqual(rec.width, 64)

    def test_rejects_non_image(self):
        with self.assertRaises(ValidationError):
            self.svc.create_from_upload(
                b"plain text", original_name="x.txt", mime_type="text/plain"
            )
        self.assertEqual(self.svc.list(), [])

    def test_rejects_empty_data(self):
        with self.assertRaises(ValidationError):
            self.svc.create_from_upload(b"", original_name="x.png")

    def test_import_path_copies_into_managed_dir(self):
        src = Path(self.tmp.name) / "ref.jpg"
        img = Image.new("RGB", (10, 20), (1, 2, 3))
        img.save(src, format="JPEG")
        rec = self.svc.import_path(src)
        self.assertEqual(rec.source, "local")
        self.assertEqual(rec.original_name, "ref.jpg")
        self.assertEqual((rec.width, rec.height), (10, 20))
        self.assertNotEqual(Path(rec.file_path), src)
        self.assertEqual(Path(rec.file_path).read_bytes(), src.read_bytes())
        # 原文件移动 / 删除后 managed asset 仍有效
        src.unlink()
        self.assertTrue(Path(self.svc.resolve_path(rec.asset_id)).is_file())

    def test_import_missing_path_raises(self):
        with self.assertRaises(AssetNotFoundError):
            self.svc.import_path(Path(self.tmp.name) / "nope.png")

    def test_get_unknown_returns_none(self):
        self.assertIsNone(self.svc.get("deadbeef"))

    def test_list_search_limit_offset(self):
        for i in range(3):
            self.svc.create_from_upload(
                make_png_bytes(), original_name=f"ref-{i}.png"
            )
        items = self.svc.list(limit=2, offset=0)
        self.assertEqual(len(items), 2)
        items2 = self.svc.list(limit=2, offset=2)
        self.assertEqual(len(items2), 1)
        self.assertEqual(len(self.svc.list(limit=999)), 3)
        hits = self.svc.list(query="ref-1")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].original_name, "ref-1.png")

    def test_list_kind_filter(self):
        self.svc.create_from_upload(make_png_bytes(), original_name="a.png", kind="reference")
        self.svc.create_from_upload(make_png_bytes(), original_name="b.png", kind="draft")
        self.assertEqual(len(self.svc.list(kind="reference")), 1)
        self.assertEqual(len(self.svc.list(kind="draft")), 1)
        self.assertEqual(len(self.svc.list()), 2)

    def test_resolve_path(self):
        rec = self.svc.create_from_upload(make_png_bytes())
        self.assertEqual(self.svc.resolve_path(rec.asset_id), rec.file_path)
        with self.assertRaises(AssetNotFoundError):
            self.svc.resolve_path("nope")

    def test_delete_unused_asset(self):
        rec = self.svc.create_from_upload(make_png_bytes())
        file_path = Path(rec.file_path)
        self.assertTrue(file_path.is_file())
        self.assertTrue(self.svc.delete(rec.asset_id))
        self.assertIsNone(self.svc.get(rec.asset_id))
        self.assertFalse(file_path.exists())
        self.assertFalse(self.svc.delete(rec.asset_id))

    def test_delete_used_asset_raises_in_use(self):
        rec = self.svc.create_from_upload(make_png_bytes())
        self.svc.attach_to_generation("gen1", rec.asset_id, "character", 0)
        with self.assertRaises(AssetInUseError):
            self.svc.delete(rec.asset_id)
        self.assertTrue(Path(rec.file_path).is_file())

    def test_attach_and_list_for_generation(self):
        a = self.svc.create_from_upload(make_png_bytes(), original_name="a.png")
        b = self.svc.create_from_upload(make_png_bytes(), original_name="b.png")
        self.svc.attach_to_generation("gen1", a.asset_id, "character", 0)
        self.svc.attach_to_generation("gen1", b.asset_id, "style", 1)
        links = self.svc.list_for_generation("gen1")
        self.assertEqual([link.asset_id for link in links], [a.asset_id, b.asset_id])
        self.assertEqual([link.role for link in links], ["character", "style"])
        self.assertEqual([link.position for link in links], [0, 1])
        self.assertEqual([link.relation for link in links], ["reference", "reference"])
        self.assertEqual(self.svc.list_for_generation("gen-none"), [])

    def test_attach_unknown_asset_raises(self):
        with self.assertRaises(AssetNotFoundError):
            self.svc.attach_to_generation("g", "nope", "character", 0)

    def test_metadata_round_trip_and_restart(self):
        rec = self.svc.create_from_upload(
            make_png_bytes(), metadata={"a": 1, "b": [1, 2], "nested": {"k": "v"}}
        )
        # 重新实例化（模拟 server / 进程重启）
        svc2 = AssetService(db_path=self.db_path, asset_dir=self.asset_dir)
        got = svc2.get(rec.asset_id)
        self.assertIsNotNone(got)
        self.assertEqual(got.metadata, {"a": 1, "b": [1, 2], "nested": {"k": "v"}})
        self.assertEqual(got.sha256, rec.sha256)
        self.assertTrue(Path(got.file_path).is_file())
        self.assertEqual(svc2.resolve_path(rec.asset_id), rec.file_path)

    def test_default_asset_dir_helper(self):
        from imagegen.config import default_asset_dir
        from imagegen.services.assets import AssetService

        svc = AssetService(db_path=self.db_path)
        self.assertEqual(svc.asset_dir, default_asset_dir())
