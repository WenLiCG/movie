from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import http.client
import os
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest


CADDYFILE = Path(__file__).resolve().parents[1] / 'gateway' / 'Caddyfile'
SETUP = Path(__file__).resolve().parents[1] / 'docs' / 'SETUP.md'
NO_STORE_HEADER = 'header >Cache-Control "private, no-store"'
NO_STORE_VALUE = 'private, no-store'
UPSTREAMS = (
    'portal:8097',
    'prowlarr:9696',
    'sonarr:8989',
    'openlist:5244',
    'qbittorrent:8080',
    'relay:8095',
)


def handle_block(config, directive):
    """Extract a handle body, including nested blocks, by matching braces."""
    opening = re.search(r'^\s*' + re.escape(directive) + r'\s*\{', config, re.M)
    if opening is None:
        raise AssertionError(f'Missing {directive} block')
    start = opening.end()
    depth = 1
    for index in range(start, len(config)):
        if config[index] == '{':
            depth += 1
        elif config[index] == '}':
            depth -= 1
            if depth == 0:
                return config[start:index]
    raise AssertionError(f'Unclosed {directive} block')


def top_level_lines(block):
    """Return direct child lines, excluding nested directive bodies."""
    lines = []
    depth = 0
    for line in block.splitlines():
        stripped = line.strip()
        if depth == 0 and stripped:
            lines.append(stripped)
        depth += line.count('{') - line.count('}')
    return lines


def find_caddy():
    env = os.environ.get('CADDY', '').strip()
    if env:
        path = Path(env)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path.resolve())
        raise FileNotFoundError(f'CADDY={env!r} is not an executable file')
    return shutil.which('caddy')


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def cache_control_values(headers):
    if hasattr(headers, 'get_all'):
        values = headers.get_all('Cache-Control') or headers.get_all('cache-control') or []
        return list(values)
    return [value for name, value in headers.items() if name.lower() == 'cache-control']


def request_https(port, path):
    context = ssl._create_unverified_context()
    connection = http.client.HTTPSConnection('127.0.0.1', port, context=context, timeout=8)
    try:
        connection.request('GET', path, headers={'Accept-Encoding': 'identity'})
        response = connection.getresponse()
        body = response.read()
        headers = response.headers
        status = response.status
        return status, headers, body
    finally:
        connection.close()


def local_caddyfile(source, site, upstream, cert, key):
    text = source
    for host in UPSTREAMS:
        text = text.replace(host, upstream)
    text = text.replace('{$MOVIE_DOMAIN}', site)
    site_open = site + ' {'
    if site_open not in text:
        raise AssertionError(f'site address {site!r} missing after substitution')
    tls_line = f'    tls {cert} {key}\n'
    text = text.replace(site_open, site_open + '\n' + tls_line, 1)
    return (
        '{\n'
        '    auto_https disable_redirects\n'
        '    admin off\n'
        '}\n\n'
        + text
    )


class CaddyProcess:
    def __init__(self, binary, config_path, listen_port, workdir):
        self.binary = binary
        self.config_path = config_path
        self.listen_port = listen_port
        self.workdir = workdir
        self.proc = None

    def __enter__(self):
        env = os.environ.copy()
        env['HOME'] = str(self.workdir)
        env['XDG_DATA_HOME'] = str(self.workdir / 'xdg-data')
        env['XDG_CONFIG_HOME'] = str(self.workdir / 'xdg-config')
        self.proc = subprocess.Popen(
            [self.binary, 'run', '--config', str(self.config_path), '--adapter', 'caddyfile'],
            cwd=str(self.workdir),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        deadline = time.time() + 8
        while time.time() < deadline:
            if self.proc.poll() is not None:
                stdout, stderr = self.proc.communicate()
                raise RuntimeError(
                    'caddy exited before listening:\n'
                    + stderr.decode('utf-8', 'replace')
                    + stdout.decode('utf-8', 'replace')
                )
            try:
                with socket.create_connection(('127.0.0.1', self.listen_port), timeout=0.1):
                    return self
            except OSError:
                time.sleep(0.05)
        self.__exit__(None, None, None)
        raise TimeoutError(f'caddy did not listen on 127.0.0.1:{self.listen_port}')

    def __exit__(self, exc_type, exc, tb):
        if self.proc is None:
            return
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.communicate(timeout=5)
        else:
            self.proc.communicate(timeout=1)


def make_self_signed(directory):
    cert = directory / 'cert.pem'
    key = directory / 'key.pem'
    result = subprocess.run(
        [
            'openssl', 'req', '-x509', '-newkey', 'rsa:2048',
            '-keyout', str(key), '-out', str(cert),
            '-days', '1', '-nodes',
            '-subj', '/CN=127.0.0.1',
            '-addext', 'subjectAltName=IP:127.0.0.1',
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError('openssl failed:\n' + result.stderr)
    return cert, key


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.config = CADDYFILE.read_text(encoding='utf-8')

    def test_site_sets_one_deferred_no_store_header_at_top_level(self):
        block = handle_block(self.config, '{$MOVIE_DOMAIN}')
        lines = top_level_lines(block)
        self.assertEqual(lines.count(NO_STORE_HEADER), 1)
        self.assertEqual(
            lines.index(NO_STORE_HEADER),
            lines.index('header Referrer-Policy "no-referrer"') + 1)

    def test_cache_control_headers_only_at_site_and_handle_errors(self):
        error_header = 'header Cache-Control "private, no-store"'
        headers = re.findall(
            r'(?m)^\s*header\s+>?Cache-Control\b[^\n]*', self.config)
        self.assertEqual(
            [header.strip() for header in headers],
            [NO_STORE_HEADER, error_header])
        errors = handle_block(self.config, 'handle_errors')
        self.assertEqual(
            [line for line in top_level_lines(errors) if 'Cache-Control' in line],
            [error_header])

    def test_handles_retain_admin_auth_and_upstreams(self):
        for directive, upstream in (
                ('handle /search/*', 'prowlarr:9696'),
                ('handle /sonarr/*', 'sonarr:8989'),
                ('handle /openlist/*', 'openlist:5244'),
                ('handle_path /qb/*', 'qbittorrent:8080')):
            with self.subTest(directive=directive):
                block = handle_block(self.config, directive)
                lines = top_level_lines(block)
                self.assertEqual(lines.count('import cinema_admin'), 1)
                self.assertRegex(
                    block, r'(?m)^\s*reverse_proxy ' + re.escape(upstream) + r'(?:\s*\{|\s*$)')

    def test_discover_retains_upstream_without_import(self):
        block = handle_block(self.config, 'handle /discover/*')
        self.assertNotRegex(block, r'(?m)^\s*import\b')
        self.assertRegex(block, r'(?m)^\s*reverse_proxy portal:8097\s*$')

    def test_setup_docs_token_rotation_requires_setup_connect(self):
        docs = SETUP.read_text(encoding='utf-8')
        if '重置后须重新运行 `configure.py --token` 或 `setup`' in docs:
            self.fail('docs still claim configure.py --token can rotate a configured install')
        sentence = re.search(r'重置后须重新运行 `setup`[^。]*', docs)
        if sentence is None or '--connect' not in sentence.group(0):
            self.fail('docs must tell operators to re-run setup (--connect) after resetting the token')

    def test_setup_docs_cache_control_covers_errors_except_auto_https(self):
        docs = SETUP.read_text(encoding='utf-8')
        if '网关对本站全部响应统一加' in docs:
            self.fail('docs still claim every response gets Cache-Control, including out-of-site redirects')
        self.assertRegex(docs, r'站点处理器的?响应')
        self.assertRegex(docs, r'错误响应')
        self.assertIn('80 端口自动 HTTPS 跳转除外', docs)


class GatewayLiveCaddyTests(unittest.TestCase):
    """Runtime checks against a real Caddy process using the production Caddyfile.

    The fixture only substitutes the site address, upstreams, a temporary
    certificate, and global options so the test can bind high ports without
    touching :80. Set CADDY or put caddy on PATH (this suite used official
    v2.11.7).
    """

    @classmethod
    def setUpClass(cls):
        cls.caddy = find_caddy()
        if not cls.caddy:
            raise unittest.SkipTest(
                'caddy binary not found; set CADDY or add caddy to PATH')

    def test_unreachable_upstream_502_has_exactly_one_no_store(self):
        source = CADDYFILE.read_text(encoding='utf-8')
        site_port = free_port()
        upstream_port = free_port()
        site = f'127.0.0.1:{site_port}'
        upstream = f'127.0.0.1:{upstream_port}'
        with tempfile.TemporaryDirectory(prefix='movie-gw-') as raw:
            workdir = Path(raw)
            cert, key = make_self_signed(workdir)
            config = local_caddyfile(source, site, upstream, cert, key)
            config_path = workdir / 'Caddyfile'
            config_path.write_text(config, encoding='utf-8')
            with CaddyProcess(self.caddy, config_path, site_port, workdir):
                status, headers, body = request_https(site_port, '/ok')
        self.assertEqual(status, 502)
        self.assertEqual(cache_control_values(headers), [NO_STORE_VALUE])
        self.assertEqual(headers.get('Referrer-Policy'), 'no-referrer')

    def test_reachable_upstream_200_still_has_exactly_one_no_store(self):
        source = CADDYFILE.read_text(encoding='utf-8')
        site_port = free_port()
        site = f'127.0.0.1:{site_port}'
        stub = ThreadingHTTPServer(('127.0.0.1', 0), _UpstreamStub)
        thread = threading.Thread(target=stub.serve_forever, daemon=True)
        thread.start()
        try:
            upstream = f'127.0.0.1:{stub.server_address[1]}'
            with tempfile.TemporaryDirectory(prefix='movie-gw-') as raw:
                workdir = Path(raw)
                cert, key = make_self_signed(workdir)
                config = local_caddyfile(source, site, upstream, cert, key)
                config_path = workdir / 'Caddyfile'
                config_path.write_text(config, encoding='utf-8')
                with CaddyProcess(self.caddy, config_path, site_port, workdir):
                    status, headers, body = request_https(site_port, '/ok')
        finally:
            stub.shutdown()
            stub.server_close()
            thread.join(timeout=5)
        self.assertEqual(status, 200)
        self.assertEqual(body, b'ok')
        self.assertEqual(cache_control_values(headers), [NO_STORE_VALUE])
        self.assertEqual(headers.get('Referrer-Policy'), 'no-referrer')


class _UpstreamStub(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def do_GET(self):
        body = b'ok'
        self.send_response(200)
        self.send_header('Cache-Control', 'public, max-age=3600')
        self.send_header('Content-Type', 'text/plain')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


if __name__ == '__main__':
    unittest.main()
