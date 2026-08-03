#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""提示词词库薄入口（兼容旧命令）：实际逻辑在 imagegen/library.py。"""

from __future__ import annotations

import sys

from imagegen.library import main


if __name__ == "__main__":
    sys.exit(main())
