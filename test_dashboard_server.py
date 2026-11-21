import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

from dashboard import DashboardData, DashboardServer, dashboard_urls


class DashboardUrlTests(unittest.TestCase):
    def test_any_interface_includes_local_and_lan(self):
        with patch("dashboard._lan_ip", return_value="192.168.1.20"):
            urls = dashboard_urls("0.0.0.0", 8765)
        self.assertIn("http://127.0.0.1:8765/", urls)
        self.assertIn("http://192.168.1.20:8765/", urls)

    def test_any_interface_without_lan_still_has_local(self):
        with patch("dashboard._lan_ip", return_value=None):
            urls = dashboard_urls("0.0.0.0", 8765)
        self.assertEqual(urls, ["http://127.0.0.1:8765/"])

    def test_loopback_is_local_only(self):
        self.assertEqual(
            dashboard_urls("127.0.0.1", 8765), ["http://127.0.0.1:8765/"]
        )

    def test_specific_host_uses_that_host(self):
        self.assertEqual(
            dashboard_urls("192.168.1.20", 8765),
            ["http://192.168.1.20:8765/"],
        )


class DashboardServerTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.dir, ignore_errors=True))
        self.data = DashboardData(
            os.path.join(self.dir, "history.json"),
            os.path.join(self.dir, "cash_events.csv"),
        )
        self.server = DashboardServer(
            host="127.0.0.1", port=0, data=self.data, auto_open=False
        )
        self.addCleanup(self.server.stop)

    def test_start_serves_api_and_can_be_stopped(self):
        self.assertTrue(self.server.start())
        self.assertIsNotNone(self.server.port)
        url = f"http://127.0.0.1:{self.server.port}/api/state"
        with urlopen(url, timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertIn(b"active_session", response.read())
        self.server.stop()
        self.assertIsNone(self.server.httpd)

    def test_start_twice_is_safe(self):
        self.assertTrue(self.server.start())
        port = self.server.port
        self.assertTrue(self.server.start())
        self.assertEqual(self.server.port, port)

    def test_start_failure_is_reported_not_raised(self):
        with patch("dashboard.ThreadingHTTPServer", side_effect=OSError("in use")):
            self.assertFalse(self.server.start())
        self.assertIsNotNone(self.server.error)
        self.assertIn("in use", str(self.server.error))


class _SessionData:
    def sessions(self):
        return [{"id": "s1", "balance": 100, "matches": 1}]

    def session(self, uid):
        if uid != "s1":
            return None
        return {
            "id": "s1",
            "balance": 100,
            "matches": [{"id": "s1-m1"}],
            "curve": [{"time": "2026-11-01T10:00:00+00:00", "balance": 0}],
        }


class DashboardSessionEndpointTests(unittest.TestCase):
    def setUp(self):
        self.server = DashboardServer(
            host="127.0.0.1", port=0, data=_SessionData(), auto_open=False
        )
        self.addCleanup(self.server.stop)
        self.assertTrue(self.server.start())
        self.base = f"http://127.0.0.1:{self.server.port}"

    def test_sessions_endpoint(self):
        with urlopen(self.base + "/api/sessions", timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertIn(b'"id": "s1"', response.read())

    def test_session_endpoint(self):
        with urlopen(self.base + "/api/session/s1", timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertIn(b'"curve"', response.read())

    def test_missing_session_is_404(self):
        with self.assertRaises(HTTPError) as caught:
            urlopen(self.base + "/api/session/missing", timeout=5)
        self.assertEqual(caught.exception.code, 404)


class _GoldStub:
    def snapshot(self):
        return {
            "ok": True,
            "price": 621425,
            "stars": 0,
            "zone": "fair",
            "history": [],
        }


class GoldEndpointTests(unittest.TestCase):
    def setUp(self):
        self.server = DashboardServer(
            host="127.0.0.1",
            port=0,
            data=_SessionData(),
            auto_open=False,
            gold=_GoldStub(),
        )
        self.addCleanup(self.server.stop)
        self.assertTrue(self.server.start())
        self.base = f"http://127.0.0.1:{self.server.port}"

    def test_gold_endpoint(self):
        with urlopen(self.base + "/api/gold", timeout=5) as response:
            payload = json.loads(response.read())
        self.assertEqual(payload["price"], 621425)
        self.assertEqual(payload["zone"], "fair")

    def test_gold_endpoint_ignores_season_parameter(self):
        with urlopen(self.base + "/api/gold?season=none", timeout=5) as response:
            payload = json.loads(response.read())
        self.assertEqual(payload["price"], 621425)


if __name__ == "__main__":
    unittest.main()
