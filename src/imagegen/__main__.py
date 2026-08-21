"""支持 ``python -m imagegen`` 独立入口。"""

from __future__ import annotations

from .cli import main


if __name__ == "__main__":
    raise SystemExit(main())
