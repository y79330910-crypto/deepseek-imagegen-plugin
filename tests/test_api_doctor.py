"""HTTP API v1 POST /doctor 集成测试。"""

from __future__ import annotations

import unittest

from imagegen.errors import UpstreamError

from .api_test_utils import ApiTestServer, FakeDiagnosticService


class TestDoctorRoute(unittest.TestCase):
    def test_doctor_returns_structured_json(self):
        result = {"ok": True, "checks": [{"ok": True, "target": "translator"}]}
        server = ApiTestServer(diagnostic_service=FakeDiagnosticService(result=result))
        self.addCleanup(server.close)
        status, _, data = server.json("POST", "/api/v1/doctor")
        self.assertEqual(status, 200)
        self.assertEqual(data, result)

    def test_doctor_error_mapped(self):
        server = ApiTestServer(
            diagnostic_service=FakeDiagnosticService(exc=UpstreamError("proxy down"))
        )
        self.addCleanup(server.close)
        status, _, data = server.json("POST", "/api/v1/doctor")
        self.assertEqual(status, 502)
        self.assertEqual(data["error"]["type"], "upstream_error")
