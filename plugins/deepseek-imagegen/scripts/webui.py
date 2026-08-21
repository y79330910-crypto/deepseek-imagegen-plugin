#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Legacy plugin WebUI launcher（兼容入口）。

WebUI 已迁移为 standalone ImageGen WebUI（src/imagegen/web）。
本脚本只负责：加载 Core → 注入 Codex 环境默认值 → 启动 imagegen serve → 打开浏览器。
不再包含任何 HTML / CSS / JS / 生成逻辑 / 旧 API server。
"""

from __future__ import annotations

import argparse
import sys
import threading
import webbrowser
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[3] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

LEGACY_NOTICE = (
    "Legacy plugin WebUI launcher is deprecated.\n"
    "Using standalone ImageGen WebUI."
)


def main(argv: list[str] | None = None) -> int:
    print(LEGACY_NOTICE)
    parser = argparse.ArgumentParser(
        prog="webui.py",
        description="启动 standalone ImageGen WebUI（旧插件入口兼容）",
    )
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=8765, help="端口（默认 8765）")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args(argv)

    from imagegen.api import create_server, validate_bind_address
    from imagegen.cli import configure_console_utf8

    try:
        import codex_adapter

        codex_adapter.prepare_environment()
    except Exception:  # noqa: BLE001
        pass
    configure_console_utf8()

    host = validate_bind_address(args.host, allow_remote=False)
    server = create_server(host, args.port)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"ImageGen WebUI 已启动：{url}（按 Ctrl+C 退出）")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
