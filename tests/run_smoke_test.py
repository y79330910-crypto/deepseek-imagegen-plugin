#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ImageGen 统一测试入口。

运行：
    python tests/run_smoke_test.py
    python -m unittest            # 等价（自动发现 tests/ 下的 test_*.py）
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.discover(
        start_dir=str(REPO_ROOT),
        pattern="test_*.py",
        top_level_dir=str(REPO_ROOT),
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
