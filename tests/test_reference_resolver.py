"""ReferenceResolver 单元测试：references → images + reference_roles。"""

from __future__ import annotations

import unittest

from imagegen.errors import AssetNotFoundError, ValidationError
from imagegen.services.references import ReferenceResolver


class FakeAssetService:
    def __init__(self, paths: dict[str, str]):
        self.paths = paths

    def resolve_path(self, asset_id: str) -> str:
        if asset_id not in self.paths:
            raise AssetNotFoundError(f"unknown asset: {asset_id}")
        return self.paths[asset_id]


class TestReferenceResolver(unittest.TestCase):
    def setUp(self):
        self.paths = {
            "a" * 32: r"D:\managed\a.png",
            "b" * 32: r"D:\managed\b.png",
        }
        self.resolver = ReferenceResolver(FakeAssetService(self.paths))

    def test_resolves_references_to_images_and_roles(self):
        out = self.resolver.resolve(
            {
                "prompt": "hello",
                "references": [
                    {"asset_id": "a" * 32, "role": "character"},
                    {"asset_id": "b" * 32, "role": "style"},
                ],
            }
        )
        self.assertEqual(out["images"], [r"D:\managed\a.png", r"D:\managed\b.png"])
        self.assertEqual(out["reference_roles"], ["character", "style"])
        self.assertNotIn("references", out)

    def test_role_defaults_to_auto(self):
        out = self.resolver.resolve(
            {"prompt": "x", "references": [{"asset_id": "a" * 32}]}
        )
        self.assertEqual(out["reference_roles"], ["auto"])

    def test_empty_references_strips_field(self):
        out = self.resolver.resolve({"prompt": "x", "references": []})
        self.assertNotIn("references", out)
        self.assertEqual(out.get("images", []), [])
        self.assertEqual(out.get("reference_roles", []), [])

    def test_no_references_passthrough(self):
        payload = {
            "prompt": "x",
            "images": ["old.png"],
            "reference_roles": ["character"],
        }
        self.assertEqual(self.resolver.resolve(payload), payload)

    def test_merges_with_legacy_images(self):
        out = self.resolver.resolve(
            {
                "prompt": "x",
                "images": ["old.png"],
                "reference_roles": [],
                "references": [{"asset_id": "a" * 32, "role": "pose"}],
            }
        )
        self.assertEqual(out["images"], ["old.png", r"D:\managed\a.png"])
        self.assertEqual(out["reference_roles"], ["pose"])

    def test_missing_asset_id_raises(self):
        with self.assertRaises(ValidationError):
            self.resolver.resolve(
                {"prompt": "x", "references": [{"role": "character"}]}
            )

    def test_unknown_role_raises(self):
        with self.assertRaises(ValidationError):
            self.resolver.resolve(
                {"prompt": "x", "references": [{"asset_id": "a" * 32, "role": "bogus"}]}
            )

    def test_unknown_asset_raises(self):
        with self.assertRaises(AssetNotFoundError):
            self.resolver.resolve(
                {"prompt": "x", "references": [{"asset_id": "zzzz"}]}
            )

    def test_more_than_four_references_raises(self):
        refs = [{"asset_id": "a" * 32, "role": "character"} for _ in range(5)]
        with self.assertRaises(ValidationError):
            self.resolver.resolve({"prompt": "x", "references": refs})

    def test_references_must_be_list(self):
        with self.assertRaises(ValidationError):
            self.resolver.resolve({"prompt": "x", "references": "not-a-list"})

    def test_reference_item_must_be_object(self):
        with self.assertRaises(ValidationError):
            self.resolver.resolve({"prompt": "x", "references": ["a" * 32]})

    def test_four_references_allowed(self):
        refs = [
            {"asset_id": "a" * 32, "role": "character"},
            {"asset_id": "b" * 32, "role": "outfit"},
            {"asset_id": "a" * 32, "role": "style"},
            {"asset_id": "b" * 32, "role": "pose"},
        ]
        out = self.resolver.resolve({"prompt": "x", "references": refs})
        self.assertEqual(len(out["images"]), 4)
        self.assertEqual(len(out["reference_roles"]), 4)
