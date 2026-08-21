#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepSeek ImageGen 薄入口（Codex Adapter）：只负责加载 Core 并调用 CLI。"""

from __future__ import annotations

import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[3] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import codex_adapter  # noqa: E402
from imagegen.cli import main  # noqa: E402


if __name__ == "__main__":
    codex_adapter.prepare_environment()
    sys.exit(main())
