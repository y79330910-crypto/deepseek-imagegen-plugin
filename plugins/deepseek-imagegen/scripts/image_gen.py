#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepSeek ImageGen v1.0.0 薄入口：实际逻辑在 imagegen/ 包。"""

from __future__ import annotations

import sys

from imagegen.cli import main


if __name__ == "__main__":
    sys.exit(main())
