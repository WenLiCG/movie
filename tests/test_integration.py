"""Offline API contract checks; these do not log into PT or cloud accounts."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("integration", ROOT / "integration.py")
integration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(integration)


class FakeAPI:
    def __init__(self, existing=None):
        self.existing = existing or []
        self.calls = []

    def json(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == "GET":
            if path.endswith("/schema"):
                return [{"implementation": "QBittorrent", "configContract": "QBittorrentSettings",
                         "fields": [{"name": "host", "value": "old"},
                                    {"name": "category", "value": ""},
                                    {"name": "initialState", "value": 0}]}]
            return self.existing
        return body


class WiringTests(unittest.TestCase):
    def test_qb_legacy_and_53_logins_require_a_session_before_wiring(self):
        for body, name, allowed in [(b"Ok.", "SID", True), (b"", "QBT_SID_8080", True),
                                    (b"", "unrelated", False), (b"Fails.", "SID", False)]:
            with self.subTest(body=body, cookie=name), patch.object(integration, "Client") as factory, \
                    patch.object(integration, "provider"), patch.object(integration, "update_config"), \
                    patch.object(integration, "ensure_categories") as categories:
                client = factory.return_value
                client.json.side_effect = lambda method, path, *args, **kwargs: (
                    [] if path.endswith('/rootfolder') else
                    {"code": 200, "data": {"is_dir": True, "provider": "AliyundriveOpen"}})
                client.request.return_value = body
                client.cookies = [SimpleNamespace(name=name, value="test-session")]
                if allowed:
                    integration.connect_services("token", "test-user", "password", "p-key", "s-key")
                    categories.assert_called_once()
                    self.assertEqual([call.args[0] for call in factory.call_args_list], [
                        'http://127.0.0.1:9696/search', 'http://127.0.0.1:8989/sonarr',
                        'http://127.0.0.1:8080', 'http://127.0.0.1:5244/openlist'])
                else:
                    with self.assertRaisesRegex(ValueError, "qB"):
                        integration.connect_services("token", "test-user", "password", "p-key", "s-key")
                    categories.assert_not_called()

    def test_hidden_passwordless_emby_user_blocks_public_and_session_is_revoked(self):
        for users, blocked in [
            ([{"Policy": {"IsHidden": True}, "HasConfiguredPassword": False, "HasPassword": False}], True),
            ([{"Policy": {}, "HasConfiguredPassword": True, "HasPassword": False}], True),
            ([{"Policy": {}, "HasConfiguredPassword": True, "HasPassword": True}], False),
        ]:
            with patch.object(integration, "Client") as factory:
                client = factory.return_value
                client.json.side_effect = [
                    {"AccessToken": "temporary-test-session", "User": {"Policy": {"IsAdministrator": True}}}, users]
                if blocked:
                    with self.assertRaises(ValueError):
                        integration.verify_emby_accounts("admin", "test-password")
                else:
                    integration.verify_emby_accounts("admin", "test-password")
                client.request.assert_called_once_with("POST", "/Sessions/Logout")
                self.assertEqual(client.json.call_args.args, ("GET", "/Users"))

    def test_provider_uses_schema_and_preserves_defaults(self):
        api = FakeAPI()
        integration.provider(api, "/api/v1", "downloadclient", "QBittorrent", "Cinema qB",
                             {"host": "qbittorrent", "category": "cinema-movies"}, enable=True)
        self.assertEqual([x[:2] for x in api.calls][-2:], [
            ("POST", "/api/v1/downloadclient/test"), ("POST", "/api/v1/downloadclient")])
        payload = api.calls[-1][2]
        self.assertTrue(payload["enable"])
        self.assertEqual({x["name"]: x["value"] for x in payload["fields"]},
                         {"host": "qbittorrent", "category": "cinema-movies", "initialState": 0})

    def test_rerun_updates_owned_record_without_duplicate(self):
        api = FakeAPI([{"id": 7, "name": "Cinema qB", "implementation": "QBittorrent",
                        "fields": [{"name": "host", "value": "old"}]}])
        integration.provider(api, "/api/v3", "downloadclient", "QBittorrent", "Cinema qB",
                             {"host": "qbittorrent"}, removeCompletedDownloads=False)
        self.assertEqual(api.calls[-1][:2], ("PUT", "/api/v3/downloadclient/7"))
        self.assertFalse(api.calls[-1][2]["removeCompletedDownloads"])

    def test_unknown_schema_or_name_collision_stops_before_write(self):
        for api, fields in [(FakeAPI(), {"missingField": 1}),
                            (FakeAPI([{"name": "Cinema qB", "implementation": "Other"}]), {})]:
            with self.assertRaises(ValueError):
                integration.provider(api, "/api/v1", "downloadclient", "QBittorrent", "Cinema qB", fields)
            self.assertTrue(all(call[0] == "GET" for call in api.calls))

    def test_category_collision_does_not_relocate_existing_data(self):
        api = FakeAPI({"sonarr-tv": {"savePath": "/somewhere/else"}})
        with self.assertRaises(ValueError):
            integration.ensure_categories(api)
        self.assertEqual(len(api.calls), 1)

    def test_unreachable_service_is_named_without_its_address(self):
        import urllib.error
        for base, prefix in [("http://sonarr:8989/sonarr", "本地应用 Sonarr 暂时"), ("http://127.0.0.1:9696/search", "本地应用 Prowlarr 暂时"),
                             ("http://qbittorrent:8080", "本地应用 qBittorrent 暂时"), ("http://203.0.113.9:1", "本地应用暂时")]:
            client = integration.Client(base)
            client.opener = SimpleNamespace(open=lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("refused")))
            with self.assertRaises(ValueError) as caught:
                client.json("GET", "/api/v3/series?apikey=secret")
            message = str(caught.exception)
            self.assertTrue(message.startswith(prefix + "无法连接"), message)
            self.assertNotIn("setup", message)  # Also shown in the portal, where "retry setup" misleads.
            for private in ("http", "secret", "8989", "203.0.113.9"):
                self.assertNotIn(private, message)

    def test_api_key_read_never_echoes_malformed_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.xml"
            path.write_text("<Config><ApiKey>private-invalid-key</ApiKey></Config>")
            with self.assertRaises(ValueError) as caught:
                integration.read_api_key(path)
            self.assertNotIn("private-invalid-key", str(caught.exception))
            path.write_text("<Config><ApiKey>" + "a" * 32 + "</ApiKey></Config>")
            self.assertEqual(integration.read_api_key(path), "a" * 32)

    def test_http_login_cookies_and_redirect_refusal(self):
        seen = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                seen.append(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                self.send_response(200)
                self.send_header("Set-Cookie", "SID=test-session; Path=/")
                self.end_headers()
                self.wfile.write(b"Ok.")

            def do_GET(self):
                seen.append(self.headers.get("Cookie"))
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "/secret-target")
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b'{"ok": true}')

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = integration.Client(f"http://127.0.0.1:{server.server_port}")
            self.assertEqual(client.request("POST", "/login", form={"password": "a&b=c"}), b"Ok.")
            self.assertIn(b"password=a%26b%3Dc", seen[0])
            self.assertTrue(client.json("GET", "/session")["ok"])
            self.assertEqual(seen[-1], "SID=test-session")
            with self.assertRaisesRegex(ValueError, "HTTP 302"):
                client.json("GET", "/redirect")
            self.assertEqual(len(seen), 3, "redirect target must not receive credentials")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
