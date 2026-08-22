"""PreviewService 测试：管线、格式、EXIF、动画、缓存命中与失效。"""

from __future__ import annotations

import tempfile
import unittest
import os
from pathlib import Path

from PIL import Image

from imagegen.services.previews import (
    PREVIEW_MAX_SIDE,
    PreviewError,
    PreviewService,
    create_webp_preview,
)


def _save(path: Path, img: Image.Image, fmt: str = "PNG", **kwargs) -> None:
    img.save(path, format=fmt, **kwargs)


class TestPreviewGeneration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _preview_size(self, src: Path, max_side: int = PREVIEW_MAX_SIDE) -> tuple[int, int]:
        dst = self.root / "out.webp"
        create_webp_preview(src, dst, max_side=max_side)
        with Image.open(dst) as img:
            self.assertEqual(img.format, "WEBP")
            return img.size

    def test_landscape(self):
        src = self.root / "land.png"
        _save(src, Image.new("RGB", (1200, 600), (200, 30, 40)))
        self.assertEqual(self._preview_size(src), (512, 256))

    def test_portrait(self):
        src = self.root / "port.png"
        _save(src, Image.new("RGB", (600, 1200), (200, 30, 40)))
        self.assertEqual(self._preview_size(src), (256, 512))

    def test_square(self):
        src = self.root / "sq.png"
        _save(src, Image.new("RGB", (800, 800), (200, 30, 40)))
        self.assertEqual(self._preview_size(src), (512, 512))

    def test_small_image_not_upscaled(self):
        src = self.root / "small.png"
        _save(src, Image.new("RGB", (64, 48), (9, 9, 9)))
        self.assertEqual(self._preview_size(src), (64, 48))

    def test_png_jpeg_webp_inputs(self):
        for name, fmt in (("a.png", "PNG"), ("a.jpg", "JPEG"), ("a.webp", "WEBP")):
            with self.subTest(fmt=fmt):
                src = self.root / name
                _save(src, Image.new("RGB", (600, 400), (1, 2, 3)), fmt=fmt)
                self.assertEqual(self._preview_size(src), (512, 341))

    def test_transparency_preserved(self):
        src = self.root / "trans.png"
        img = Image.new("RGBA", (600, 400), (255, 0, 0, 0))
        _save(src, img)
        dst = self.root / "out.webp"
        create_webp_preview(src, dst)
        with Image.open(dst) as out:
            self.assertEqual(out.mode, "RGBA")
            self.assertEqual(out.getpixel((0, 0))[3], 0)

    def test_exif_orientation_applied(self):
        src = self.root / "exif.jpg"
        img = Image.new("RGB", (100, 200), (200, 30, 40))
        exif = Image.Exif()
        exif[274] = 6  # rotate 270 CW
        img.save(src, exif=exif)
        self.assertEqual(self._preview_size(src), (200, 100))

    def test_animated_gif_first_frame(self):
        src = self.root / "anim.gif"
        frames = [
            Image.new("RGB", (600, 400), (255, 0, 0)),
            Image.new("RGB", (600, 400), (0, 255, 0)),
        ]
        frames[0].save(
            src, save_all=True, append_images=frames[1:], duration=100, loop=0
        )
        dst = self.root / "out.webp"
        create_webp_preview(src, dst)
        with Image.open(dst) as out:
            self.assertEqual(out.size, (512, 341))
            r, g, b = out.convert("RGB").getpixel((0, 0))
            self.assertGreater(r, 180)
            self.assertLess(g, 80)


class TestPreviewCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.src = self.root / "src.png"
        _save(self.src, Image.new("RGB", (900, 900), (10, 20, 30)))
        self.svc = PreviewService(cache_root=self.root / "cache")

    def test_cache_hit_returns_same_file(self):
        first = self.svc.generation_thumbnail("gen-a", self.src)
        second = self.svc.generation_thumbnail("gen-a", self.src)
        self.assertEqual(first, second)
        self.assertTrue(first.is_file())

    def test_source_change_invalidates_cache(self):
        first = self.svc.generation_thumbnail("gen-a", self.src)
        _save(self.src, Image.new("RGB", (700, 700), (5, 6, 7)))
        # 强制 mtime 前进，确保 identity（size + mtime_ns）发生变化
        now_ns = os.stat(self.src).st_mtime_ns + 5_000_000_000
        os.utime(self.src, ns=(now_ns, now_ns))
        second = self.svc.generation_thumbnail("gen-a", self.src)
        self.assertNotEqual(first, second)
        with Image.open(second) as img:
            self.assertEqual(img.size, (512, 512))

    def test_invalidate_generation_and_asset(self):
        self.svc.generation_thumbnail("gen-a", self.src)
        asset_src = self.root / "asset.png"
        _save(asset_src, Image.new("RGB", (500, 500), (1, 2, 3)))
        self.svc.asset_thumbnail("asset-a", asset_src)
        self.svc.invalidate_generation("gen-a")
        self.assertEqual(
            list((self.svc.cache_root / "generations").glob("gen-a__*")), []
        )
        self.assertTrue(
            list((self.svc.cache_root / "assets").glob("asset-a__*"))
        )
        self.svc.invalidate_asset("asset-a")
        self.assertEqual(
            list((self.svc.cache_root / "assets").glob("asset-a__*")), []
        )

    def test_missing_source_raises(self):
        with self.assertRaises(PreviewError):
            self.svc.generation_thumbnail("gen-missing", self.root / "nope.png")

    def test_does_not_modify_source(self):
        before = self.src.read_bytes()
        self.svc.generation_thumbnail("gen-a", self.src)
        self.assertEqual(self.src.read_bytes(), before)
