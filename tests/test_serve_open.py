"""imagegen serve --open 行为测试（mock 浏览器与 server）。"""

from __future__ import annotations

import argparse
import unittest
from unittest import mock

from imagegen.cli import cmd_serve


class FakeServer:
    def serve_forever(self):
        raise KeyboardInterrupt

    def server_close(self):
        pass


class TestServeOpen(unittest.TestCase):
    def _args(self, open_browser: bool):
        return argparse.Namespace(
            host="127.0.0.1",
            port=8765,
            config="",
            allow_remote=False,
            open=open_browser,
        )

    def test_open_opens_browser_after_bind(self):
        events = []
        fake = FakeServer()

        def fake_create(host, port, config_path=None, **kwargs):
            events.append("server-created")
            return fake

        with (
            mock.patch("imagegen.api.create_server", side_effect=fake_create),
            mock.patch("imagegen.cli.webbrowser.open", side_effect=lambda url: events.append("opened:" + url)),
        ):
            code = cmd_serve(self._args(open_browser=True))
        self.assertEqual(code, 0)
        self.assertEqual(events[0], "server-created")
        self.assertEqual(events[1], "opened:http://127.0.0.1:8765/")

    def test_without_open_does_not_open_browser(self):
        with (
            mock.patch("imagegen.api.create_server", return_value=FakeServer()),
            mock.patch("imagegen.cli.webbrowser.open") as open_browser,
        ):
            code = cmd_serve(self._args(open_browser=False))
        self.assertEqual(code, 0)
        open_browser.assert_not_called()
