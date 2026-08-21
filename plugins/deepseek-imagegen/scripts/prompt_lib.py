#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""提示词词库薄入口（兼容旧命令）：实际逻辑在 src/imagegen/library.py。"""

from __future__ import annotations

import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[3] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from imagegen.library import main  # noqa: E402


if __name__ == "__main__":
    sys.exit(main())
