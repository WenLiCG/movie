import contextlib
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from scripts.check_public_login import check_public_login


class PublicLoginTests(unittest.TestCase):
    def test_public_login_and_redacted_failures_over_http(self):
        portal = (Path(__file__).resolve().parents[1]/'portal/index.html').read_bytes()
        pages = {
            '/web/index.html': (200, 'text/html', b'<html><title>Emby</title></html>', ''),
            '/discover/': (200, 'text/html', portal, ''),
            '/discover/api/session': (401, 'application/json', b'{"error":"login required"}', ''),
        }
        visits = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                visits.append((self.path, self.headers.get('Cookie'), self.headers.get('Authorization')))
                status, mime, body, location = pages.get(self.path, (302, 'text/plain', b'', '../discover/'))
                self.send_response(status)
                self.send_header('Content-Type', mime)
                self.send_header('Set-Cookie', 'session=must-not-be-reused; Path=/')
                if location:
                    self.send_header('Location', location)
                self.end_headers()
                self.wfile.write(body)

        with ThreadingHTTPServer(('127.0.0.1',0), Handler) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            self.addCleanup(worker.join, 2)
            try:
                origin = 'https://127.0.0.1:'+str(server.server_port)
                output = io.StringIO()
                with patch('scripts.check_public_login.HTTPSConnection', HTTPConnection), contextlib.redirect_stdout(output):
                    self.assertTrue(check_public_login(origin+'/'))
                    self.assertEqual(len(visits), 7)
                    self.assertTrue(all(cookie is None and auth is None for _,cookie,auth in visits))
                    # An Access login page and arbitrary redirect both used to look healthy when only checking HTTP.
                    pages['/web/index.html'] = (200, 'text/html', b'<title>Sign in to Emby - Cloudflare Access</title>', '')
                    pages['/discover/'] = (200, 'text/html', b'<form>Sign in</form>', '')
                    pages['/discover/api/session'] = (401, 'text/html', b'private-response', '')
                    pages['/qb/'] = (302, 'text/plain', b'', 'https://outside.invalid/discover/?signed=private-link')
                    self.assertFalse(check_public_login(origin))
                self.assertEqual(len(visits), 14)
                for path in ('/web/index.html', '/discover/', '/discover/api/session', '/qb/'):
                    self.assertIn('失败 '+path, output.getvalue())
                self.assertNotIn('private-', output.getvalue())
                self.assertNotIn('outside.invalid', output.getvalue())
            finally:
                server.shutdown()

    def test_requires_a_bare_https_origin_without_echoing_input(self):
        for value in ('http://cinema.test','https://user:private@cinema.test','https://cinema.test/?private',
                      'https://cinema.test/path','https://cinema.test:bad'):
            with self.subTest(), self.assertRaises(ValueError) as raised:
                check_public_login(value)
            self.assertNotIn(value, str(raised.exception))
