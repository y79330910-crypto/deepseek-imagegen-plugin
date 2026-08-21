"""兼容旧入口：诊断逻辑已迁移到 imagegen.services.diagnostics。"""

from __future__ import annotations

import argparse
from typing import Any, Optional

from .services.diagnostics import (
    DiagnosticService,
    run_size_probe,
    save_probe_cache,
)


def cmd_doctor(args: argparse.Namespace) -> dict[str, Any]:
    """兼容旧调用：委托给 DiagnosticService。"""
    return DiagnosticService().doctor(
        size_probe=getattr(args, "size_probe", False),
        size=getattr(args, "size", ""),
    )
