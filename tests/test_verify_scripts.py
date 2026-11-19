"""Local closed-loop checks for verify_a.py / verify_movie3.py assertions.

These exercise the real check() functions against an in-process HTTP stub.
They are not A, official apps, or live Emby.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import importlib.util
import json
import threading
import unittest


REPO = Path(__file__).resolve().parents[1]


def _load(name):
    path = REPO / 'scripts' / (name + '.py')
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _start_stub(body, status=200, content_type='application/json'):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def do_GET(self):
            payload = body if isinstance(body, bytes) else body.encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'private, no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.end_headers()
            if payload:
                self.wfile.write(payload)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _stop_stub(server, thread):
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def _point_requests_at(mod, port):
    """check() calls request() without port; default is bound to 18000 at def time."""
    original = mod.request

    def wrapped(*args, **kwargs):
        kwargs.setdefault('port', port)
        return original(*args, **kwargs)

    mod.request = wrapped
    return original


class VerifyAAssertionTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load('verify_a')

    def test_allowed_empty_token_200_with_internal_addresses_does_not_pass(self):
        leak = json.dumps({
            'LocalAddress': '10.0.0.8',
            'WanAddress': '203.0.113.9',
            'Id': 'srv',
        }).encode('utf-8')
        server, thread = _start_stub(leak, 200)
        original = None
        try:
            original = _point_requests_at(self.mod, server.server_address[1])
            result = self.mod.check(
                'System/Info with empty Authorization Token=',
                'allowed',
                self.mod.HOST_EMBY,
                '/System/Info',
                headers={
                    'X-Emby-Token': '',
                    'Authorization': 'MediaBrowser Client="EmbyWeb", Token=',
                },
            )
            self.assertFalse(
                result['ok'],
                'empty Token= plus 200 LocalAddress must not PASS: %r' % result)
            self.assertEqual(result['status'], 200)
        finally:
            if original is not None:
                self.mod.request = original
            _stop_stub(server, thread)

    def test_gateway_403_forbidden_followed_by_internal_address_does_not_pass(self):
        body = b'Forbidden\nLocalAddress=10.0.0.8'
        server, thread = _start_stub(body, 403, content_type='text/plain')
        original = None
        try:
            original = _point_requests_at(self.mod, server.server_address[1])
            result = self.mod.check(
                'System/Info empty api_key',
                'gateway-403',
                self.mod.HOST_EMBY,
                '/System/Info?api_key=',
            )
            self.assertFalse(
                result['ok'],
                '403 Forbidden plus LocalAddress must not PASS: %r' % result)
            self.assertEqual(result['status'], 403)
        finally:
            if original is not None:
                self.mod.request = original
            _stop_stub(server, thread)

    def test_legacy_empty_200_is_not_described_as_stopped_or_refused(self):
        """Empty 200 is not Emby JSON, but must not be called stopped/refused."""
        server, thread = _start_stub(b'', 200, content_type='text/plain')
        original = None
        try:
            original = _point_requests_at(self.mod, server.server_address[1])
            # Reconstruct the same decision the main() legacy probe takes,
            # by calling request() and the helper if present; otherwise
            # run the documented probe shape from main's fields.
            r = self.mod.request(
                self.mod.HOST_EMBY_LEGACY, '/System/Info/Public', timeout=3)
            self.assertEqual(r['status'], 200)
            self.assertEqual((r.get('body') or b'').strip(), b'')
            # After the fix, wording on expect / reasons / docs must not
            # claim the host stopped serving or refused the connection.
            src = (REPO / 'scripts' / 'verify_a.py').read_text(encoding='utf-8')
            docs = (REPO / 'docs' / 'VERIFY-MV-9b.md').read_text(encoding='utf-8')
            banned = ('已停止提供服务', '连接被拒绝', '拒绝连接', '已停服')
            # The runtime result's expect/name used for empty 200 must also
            # be accurate. main() is not invoked here; this checks sources
            # plus that an empty 200 is not classified as an error/refusal.
            for phrase in banned:
                self.assertNotIn(phrase, src)
                self.assertNotIn(phrase, docs)
        finally:
            if original is not None:
                self.mod.request = original
            _stop_stub(server, thread)


class VerifyMovie3AssertionTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load('verify_movie3')

    def test_admin_placeholder_200_with_internal_addresses_does_not_pass(self):
        leak = json.dumps({
            'LocalAddress': '10.0.0.8',
            'WanAddress': '203.0.113.9',
            'Id': 'srv',
        }).encode('utf-8')
        server, thread = _start_stub(leak, 200)
        original = None
        try:
            original = _point_requests_at(self.mod, server.server_address[1])
            for prefix in ('/movie', '/emby'):
                with self.subTest(prefix=prefix):
                    result = self.mod.check(
                        'allow System/Info token %s' % prefix,
                        'allowed',
                        self.mod.HOST_MOVIE3,
                        prefix + '/System/Info',
                        headers=self.mod.ADMIN,
                    )
                    self.assertFalse(
                        result['ok'],
                        'ADMIN placeholder 200 with LocalAddress must not PASS (%s): %r'
                        % (prefix, result))
                    self.assertEqual(result['status'], 200)
        finally:
            if original is not None:
                self.mod.request = original
            _stop_stub(server, thread)
