"""ReferenceResolver：HTTP references（asset_id）→ Core GenerateRequest 契约。

asset_id 必须在进入 Core 之前解析成 managed 本地图片路径；
GenerateRequest / Engine 永远不知道 asset_id。
"""

from __future__ import annotations

from typing import Any, Mapping

from ..errors import ValidationError
from ..reference import MAX_REF_IMAGES, validate_ref_type


class ReferenceResolver:
    """把 assets 引用解析为 images + reference_roles（HTTP / Application 边界组件）。"""

    def __init__(self, asset_service: Any):
        self._assets = asset_service

    def resolve(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise ValidationError("references 请求需要 dict/Mapping 输入。")
        references = payload.get("references")
        resolved = dict(payload)
        resolved.pop("references", None)
        if not references:
            return resolved
        if not isinstance(references, list):
            raise ValidationError("references 必须是数组。")
        if len(references) > MAX_REF_IMAGES:
            raise ValidationError(
                f"参考图最多支持 {MAX_REF_IMAGES} 张，当前收到 {len(references)} 张。"
            )
        existing_images = payload.get("images")
        images = list(existing_images) if isinstance(existing_images, list) else []
        existing_roles = payload.get("reference_roles")
        roles = list(existing_roles) if isinstance(existing_roles, list) else []
        for ref in references:
            if not isinstance(ref, Mapping):
                raise ValidationError("references 每一项必须是对象。")
            asset_id = str(ref.get("asset_id") or "").strip()
            if not asset_id:
                raise ValidationError("references 每项必须包含 asset_id。")
            role = validate_ref_type(str(ref.get("role") or "auto"))
            # AssetNotFoundError → 404；在进入 Core 前完成解析
            images.append(self._assets.resolve_path(asset_id))
            roles.append(role)
        resolved["images"] = images
        resolved["reference_roles"] = roles
        return resolved
