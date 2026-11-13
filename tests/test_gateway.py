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


def local_caddyfile(source, site, upstream, cert, key, emby_site=None):
    text = source
    for host in UPSTREAMS:
        text = text.replace(host, upstream)
    text = text.replace('{$MOVIE_DOMAIN}', site)
    if '{$EMBY_DOMAIN}' in text:
        if emby_site is None:
            emby_site = '127.0.0.1:%d' % free_port()
        text = text.replace('{$EMBY_DOMAIN}', emby_site)
    tls_line = '    tls %s %s\n' % (cert, key)
    addresses = [site]
    if emby_site is not None:
        addresses.append(emby_site)
    for addr in addresses:
        site_open = addr + ' {'
        if site_open not in text:
            raise AssertionError('site address %r missing after substitution' % (addr,))
        text = text.replace(site_open, site_open + '\n' + tls_line, 1)
    return (
        '{\n'
        '    auto_https disable_redirects\n'
        '    admin off\n'
        '}\n\n'
        + text
    )


def header_values(headers, name):
    lower = name.lower()
    if hasattr(headers, 'get_all'):
        values = headers.get_all(name) or headers.get_all(lower) or []
        return list(values)
    if hasattr(headers, 'items'):
        return [value for key, value in headers.items() if key.lower() == lower]
    return [value for key, value in headers if key.lower() == lower]


def request_https(port, path, method='GET', headers=None, body=None, raw_path=None):
    """HTTPS request to 127.0.0.1:port. raw_path sends the request-target verbatim."""
    context = ssl._create_unverified_context()
    hdrs = {'Accept-Encoding': 'identity'}
    if headers:
        hdrs.update(headers)
    if raw_path is None:
        connection = http.client.HTTPSConnection('127.0.0.1', port, context=context, timeout=8)
        try:
            connection.request(method, path, body=body, headers=hdrs)
            response = connection.getresponse()
            data = response.read()
            result_headers = response.headers
            status = response.status
            return status, result_headers, data
        finally:
            connection.close()
    sock = socket.create_connection(('127.0.0.1', port), timeout=8)
    ssock = context.wrap_socket(sock, server_hostname='127.0.0.1')
    try:
        payload = body or b''
        if not isinstance(payload, bytes):
            payload = payload.encode('utf-8')
        req = '%s %s HTTP/1.1\r\nHost: 127.0.0.1:%s\r\nConnection: close\r\n' % (
            method, raw_path, port)
        names = {key.lower() for key in hdrs}
        for key, value in hdrs.items():
            req += '%s: %s\r\n' % (key, value)
        if payload and 'content-length' not in names:
            req += 'Content-Length: %d\r\n' % len(payload)
        req += '\r\n'
        ssock.sendall(req.encode('latin1') + payload)
        buf = b''
        while True:
            chunk = ssock.recv(8192)
            if not chunk:
                break
            buf += chunk
    finally:
        ssock.close()
    head, _, data = buf.partition(b'\r\n\r\n')
    lines = head.decode('latin1', 'replace').split('\r\n')
    status = int(lines[0].split()[1])
    pairs = []
    for line in lines[1:]:
        if ':' in line:
            key, value = line.split(':', 1)
            pairs.append((key.strip(), value.strip()))

    class _Hdr(dict):
        def get_all(self, name):
            lower = name.lower()
            return [v for k, v in self._pairs if k.lower() == lower]

        def get(self, name, default=None):
            vals = self.get_all(name)
            return vals[0] if vals else default

        def items(self):
            return list(self._pairs)

    hdr = _Hdr()
    hdr._pairs = pairs
    for key, value in pairs:
        dict.__setitem__(hdr, key, value)
    return status, hdr, data

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
            lines.index('header >Referrer-Policy "no-referrer"') + 1)

    def test_cache_control_headers_only_at_site_and_handle_errors(self):
        # Scope to {$MOVIE_DOMAIN}: the public Emby site has additional cache rules.
        movie = handle_block(self.config, '{$MOVIE_DOMAIN}')
        error_header = 'header Cache-Control "private, no-store"'
        headers = re.findall(
            r'(?m)^\s*header\s+>?Cache-Control\b[^\n]*', movie)
        self.assertEqual(
            [header.strip() for header in headers],
            [NO_STORE_HEADER, error_header])
        errors = handle_block(movie, 'handle_errors')
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



    def test_upstream_referrer_policy_is_replaced_and_appears_once(self):
        """Upstream also sends Referrer-Policy; the gateway must leave exactly one no-referrer."""
        source = CADDYFILE.read_text(encoding='utf-8')
        site_port = free_port()
        site = '127.0.0.1:%d' % site_port
        stub = ThreadingHTTPServer(('127.0.0.1', 0), _UpstreamStubWithReferrer)
        thread = threading.Thread(target=stub.serve_forever, daemon=True)
        thread.start()
        try:
            upstream = '127.0.0.1:%d' % stub.server_address[1]
            with tempfile.TemporaryDirectory(prefix='movie-gw-rp-') as raw:
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
        self.assertEqual(header_values(headers, 'Referrer-Policy'), ['no-referrer'])


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



class _UpstreamStubWithReferrer(_UpstreamStub):
    def do_GET(self):
        body = b'ok'
        self.send_response(200)
        self.send_header('Cache-Control', 'public, max-age=3600')
        self.send_header('Referrer-Policy', 'origin')
        self.send_header('Content-Type', 'text/plain')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)



USER_ID = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
USER_UUID = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
ITEM_ID = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
VIDEO_ID = 'cccccccccccccccccccccccccccccccc'
AUDIO_ID = 'dddddddddddddddddddddddddddddddd'
SHOW_ID = 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee'
ADMIN_HEADERS = {
    'X-Emby-Token': 'admin-secret-token',
    'Authorization': 'MediaBrowser Client="EmbyWeb", Token=admin-secret-token',
    'Cookie': 'emby-auth=admin-secret-token',
}
NO_STORE = 'private, no-store'
IMAGE_SHORT = 'private, max-age=300'
PUBLIC_STATIC_PREFIX = 'public,'

# Whitelist: one row per public-interface behaviour, exercised live against Caddy.
# Prefixes /emby/ and /mediabrowser/ are additional cases, not a substitute for the bare path.
ALLOW_CASES = [
    ('login AuthenticateByName POST', 'POST', '/Users/AuthenticateByName'),
    ('current user Me', 'GET', '/Users/Me'),
    ('current user Me trailing slash', 'GET', '/Users/Me/'),
    ('current user by hex id', 'GET', '/Users/' + USER_ID),
    ('current user by uuid', 'GET', '/Users/' + USER_UUID),
    ('movie wall Me/Items', 'GET', '/Users/Me/Items'),
    ('movie wall Me/Items/Latest', 'GET', '/Users/Me/Items/Latest'),
    ('movie wall Me/Items/Resume', 'GET', '/Users/Me/Items/Resume'),
    ('movie wall Me/Views', 'GET', '/Users/Me/Views'),
    ('movie wall id/Items', 'GET', '/Users/' + USER_ID + '/Items'),
    ('movie wall id/Items/Latest', 'GET', '/Users/' + USER_ID + '/Items/Latest'),
    ('movie wall id/Items/Resume', 'GET', '/Users/' + USER_ID + '/Items/Resume'),
    ('movie wall id/Views', 'GET', '/Users/' + USER_ID + '/Views'),
    ('item details', 'GET', '/Items/' + ITEM_ID),
    ('item image Primary', 'GET', '/Items/' + ITEM_ID + '/Images/Primary'),
    ('item image Backdrop index', 'GET', '/Items/' + ITEM_ID + '/Images/Backdrop/0'),
    ('PlaybackInfo GET', 'GET', '/Items/' + ITEM_ID + '/PlaybackInfo'),
    ('PlaybackInfo POST', 'POST', '/Items/' + ITEM_ID + '/PlaybackInfo'),
    ('video stream', 'GET', '/Videos/' + VIDEO_ID + '/stream'),
    ('video stream container', 'GET', '/Videos/' + VIDEO_ID + '/stream.mkv'),
    ('video hls master', 'GET', '/Videos/' + VIDEO_ID + '/master.m3u8'),
    ('video hls segment', 'GET', '/Videos/' + VIDEO_ID + '/hls1/main/0.ts'),
    ('video subtitles', 'GET', '/Videos/' + VIDEO_ID + '/Subtitles/0/Stream.vtt'),
    ('audio stream', 'GET', '/Audio/' + AUDIO_ID + '/stream'),
    ('progress Playing POST', 'POST', '/Sessions/Playing'),
    ('progress Progress POST', 'POST', '/Sessions/Playing/Progress'),
    ('progress Stopped POST', 'POST', '/Sessions/Playing/Stopped'),
    ('progress Ping POST', 'POST', '/Sessions/Playing/Ping'),
    ('System/Info/Public', 'GET', '/System/Info/Public'),
    ('System/Info/Public trailing slash', 'GET', '/System/Info/Public/'),
    ('System/Ping', 'GET', '/System/Ping'),
    ('TV show episodes', 'GET', '/Shows/' + SHOW_ID + '/Episodes'),
    ('TV show seasons', 'GET', '/Shows/' + SHOW_ID + '/Seasons'),
    ('TV show NextUp', 'GET', '/Shows/' + SHOW_ID + '/NextUp'),
    ('web root', 'GET', '/web/'),
    ('web index', 'GET', '/web/index.html'),
    ('web versioned js', 'GET', '/web/main.js?v=4.10.2.0'),
    ('prefix emby current user', 'GET', '/emby/Users/Me'),
    ('prefix mediabrowser current user', 'GET', '/mediabrowser/Users/Me'),
    ('prefix emby login', 'POST', '/emby/Users/AuthenticateByName'),
    ('prefix mediabrowser PlaybackInfo', 'POST', '/mediabrowser/Items/' + ITEM_ID + '/PlaybackInfo'),
    ('prefix emby stream', 'GET', '/emby/Videos/' + VIDEO_ID + '/stream'),
    ('prefix emby Info/Public', 'GET', '/emby/System/Info/Public'),
    ('prefix mediabrowser Info/Public', 'GET', '/mediabrowser/System/Info/Public'),
    ('case mixed Users/Me', 'GET', '/users/me'),
    ('case mixed System/Info/Public', 'GET', '/system/info/public'),
    ('case mixed EMBY prefix', 'GET', '/Emby/Users/Me'),
    ('case mixed MediaBrowser prefix', 'GET', '/MediaBrowser/System/Info/Public'),
    ('case mixed ITEMS', 'GET', '/USERS/ME/ITEMS'),
    ('encoded letter U in Users', 'GET', '/%55sers/Me'),
]

DENY_403_CASES = [
    ('list all users', 'GET', '/Users'),
    ('list all users trailing slash', 'GET', '/Users/'),
    ('create user New', 'GET', '/Users/New'),
    ('Users/Public enumeration', 'GET', '/Users/Public'),
    ('user Policy', 'GET', '/Users/' + USER_ID + '/Policy'),
    ('Me Policy', 'GET', '/Users/Me/Policy'),
    ('System/Info (not Public)', 'GET', '/System/Info'),
    ('System/Info trailing slash', 'GET', '/System/Info/'),
    ('System/Configuration', 'GET', '/System/Configuration'),
    ('System/Logs', 'GET', '/System/Logs'),
    ('Plugins', 'GET', '/Plugins'),
    ('Plugins subpath', 'GET', '/Plugins/Foo'),
    ('ScheduledTasks', 'GET', '/ScheduledTasks'),
    ('Library/Refresh', 'GET', '/Library/Refresh'),
    ('Library/VirtualFolders', 'GET', '/Library/VirtualFolders'),
    ('Startup wizard', 'GET', '/Startup/Wizard/Complete'),
    ('Auth/Keys', 'GET', '/Auth/Keys'),
    ('Auth/Providers', 'GET', '/Auth/Providers'),
    ('Devices', 'GET', '/Devices'),
    ('Environment', 'GET', '/Environment'),
    ('Packages', 'GET', '/Packages'),
    ('Encoding', 'GET', '/Encoding'),
    ('list Sessions', 'GET', '/Sessions'),
    ('Sessions/Capabilities', 'GET', '/Sessions/Capabilities'),
    ('embywebsocket', 'GET', '/embywebsocket'),
    ('emby/embywebsocket', 'GET', '/emby/embywebsocket'),
    ('mediabrowser/embywebsocket', 'GET', '/mediabrowser/embywebsocket'),
    ('case list users', 'GET', '/users'),
    ('case New', 'GET', '/USERS/NEW'),
    ('prefix emby list users', 'GET', '/emby/Users'),
    ('prefix emby New', 'GET', '/emby/Users/New'),
    ('prefix mediabrowser Plugins', 'GET', '/mediabrowser/Plugins'),
    ('case EMBY PLUGINS', 'GET', '/EMBY/PLUGINS'),
    ('Plugins with api_key', 'GET', '/Plugins?api_key=ADMIN'),
    ('Users with api_key', 'GET', '/Users?api_key=ADMIN'),
    ('System/Info with api_key', 'GET', '/System/Info?api_key=ADMIN'),
    ('dot-dot to System/Info', 'GET', '/System/Info/Public/../Info'),
    ('dot-dot to Plugins via web', 'GET', '/web/../Plugins'),
    ('Items Download', 'GET', '/Items/' + ITEM_ID + '/Download'),
    ('Items remote search (admin)', 'GET', '/Items/' + ITEM_ID + '/RemoteSearch'),
    ('Users/Authenticate (not ByName)', 'POST', '/Users/Authenticate'),
    ('Sessions/Logout', 'POST', '/Sessions/Logout'),
    ('openlist root', 'GET', '/openlist'),
    ('openlist slash', 'GET', '/openlist/'),
    ('openlist api', 'GET', '/openlist/api'),
    ('openlist p with sign', 'GET', '/openlist/p/aliyun/Movies/x.mkv?sign=x'),
    ('prefix emby openlist', 'GET', '/emby/openlist/'),
]


DENY_404_CASES = [
    '/discover',
    '/discover/',
    '/discover/foo',
    '/search',
    '/search/',
    '/sonarr',
    '/sonarr/',
    '/qb',
    '/qb/',
    '/ge2o',
    '/ge2o/',
    '/ge2o/custom.js',
    '/ge2o/custom.css',
    '/web/dashboard',
    '/web/dashboard.html',
    '/web/dashboard/foo',
    '/web/configurationpage',
    '/emby/discover',
    '/emby/web/dashboard',
    '/mediabrowser/ge2o',
    '/DISCOVER',
    '/WEB/DASHBOARD',
    '/Search/foo',
    '/mediabrowser/sonarr/',
]

# Raw request-targets that must never reach relay (defense-in-depth).
DANGEROUS_RAW = [
    '/Users%2FMe',
    '/Users%2fNew',
    '/System/Info%2FPublic',
    '/emby/%2e%2e/Users/Me',
    '/foo/%2e%2e/Plugins',
    '/Users/Me;',
    '/Users/Me;foo=bar',
    '/System/Info/Public;',
    '/emby//Users/Me',
    '/Users//Me',
    '//Users/Me',
    '/Plugins%2fFoo',
    '/Users/New%2e',
    '/%2e%2e/Users/Me',
    '/.%2e/Plugins',
    '/foo/%2E%2E/System/Info',
    '/Users%5cMe',
]


def make_recording_stub():
    records = []
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def do_GET(self):
            self._go()

        def do_POST(self):
            self._go()

        def do_HEAD(self):
            self._go(write_body=False)

        def do_OPTIONS(self):
            self._go()

        def _go(self, write_body=True):
            length = int(self.headers.get('Content-Length') or 0)
            if length:
                self.rfile.read(length)
            with lock:
                records.append({
                    'method': self.command,
                    'path': self.path,
                    'raw': self.raw_requestline.decode('latin1'),
                })
            data = b'UPSTREAM:' + self.command.encode() + b':' + self.path.encode()
            self.send_response(200)
            self.send_header('Cache-Control', 'public, max-age=999')
            self.send_header('Referrer-Policy', 'origin')
            self.send_header('Content-Type', 'text/plain')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            if write_body:
                self.wfile.write(data)

        def log_message(self, format, *args):
            pass

    return Handler, records, lock


class EmbyPublicGatewayDocTests(unittest.TestCase):
    """Structural and documentation checks for the public Emby site (MV-9)."""

    def setUp(self):
        self.config = CADDYFILE.read_text(encoding='utf-8')

    def test_emby_site_block_exists_and_uses_env_domain(self):
        block = handle_block(self.config, '{$EMBY_DOMAIN}')
        self.assertTrue(block.strip())
        self.assertIn('reverse_proxy relay:8095', block)
        self.assertNotIn('forward_auth', block)
        self.assertNotIn('import cinema_admin', block)
        # Domain is not hard-coded.
        self.assertNotIn('g60.net', self.config)
        self.assertNotIn('emby.g60', self.config)

    def test_emby_site_does_not_expose_management_apps(self):
        block = handle_block(self.config, '{$EMBY_DOMAIN}')
        for upstream in ('portal:8097', 'prowlarr:9696', 'sonarr:8989',
                         'qbittorrent:8080'):
            self.assertNotIn(upstream, block)
        # D4: only the signed GET/HEAD /openlist/d/ handle may proxy openlist.
        self.assertIn('openlist:5244', block)
        self.assertIn('path_regexp ol_direct', block)
        self.assertNotIn('forward_auth', block)
        self.assertNotIn('import cinema_admin', block)
        # /openlist/api and /openlist/p are not given their own proxy handle.
        self.assertNotRegex(block, r'(?m)^\s*handle /openlist/api')
        self.assertNotRegex(block, r'(?m)^\s*handle /openlist/p')

    def test_movie_domain_site_still_present(self):
        block = handle_block(self.config, '{$MOVIE_DOMAIN}')
        self.assertIn('reverse_proxy portal:8097', block)
        self.assertIn('reverse_proxy relay:8095', block)
        self.assertIn('import cinema_admin', block)

    def test_setup_docs_describe_emby_domain_and_whitelist(self):
        docs = SETUP.read_text(encoding='utf-8')
        self.assertIn('EMBY_DOMAIN', docs)
        self.assertIn('白名单', docs)
        self.assertRegex(docs, r'不经过.*Access|不走 Access')
        self.assertIn('AuthenticateByName', docs)
        self.assertIn('System/Info/Public', docs)
        self.assertIn('System/Ping', docs)
        self.assertIn('Users/Public', docs)
        self.assertIn('MV-11', docs)
        self.assertIn('/Sessions/Playing', docs)
        self.assertIn('embywebsocket', docs)
        self.assertRegex(docs, r'#!/dashboard|hash 路由')

    def test_caddyfile_validates_with_official_binary(self):
        caddy = find_caddy()
        if not caddy:
            self.skipTest('caddy binary not found; set CADDY or add caddy to PATH')
        env = os.environ.copy()
        env['MOVIE_DOMAIN'] = 'movie.example.com'
        env['EMBY_DOMAIN'] = 'emby.example.com'
        result = subprocess.run(
            [caddy, 'validate', '--config', str(CADDYFILE), '--adapter', 'caddyfile'],
            capture_output=True, text=True, env=env,
        )
        combined = (result.stdout or '') + (result.stderr or '')
        self.assertEqual(result.returncode, 0, combined)
        self.assertIn('Valid configuration', combined)


class _EmbyLiveBase(unittest.TestCase):
    """Shared live Caddy fixture: both sites, recording upstream stub."""

    @classmethod
    def setUpClass(cls):
        cls.caddy = find_caddy()
        if not cls.caddy:
            raise unittest.SkipTest(
                'caddy binary not found; set CADDY or add caddy to PATH')
        source = CADDYFILE.read_text(encoding='utf-8')
        if '{$EMBY_DOMAIN}' not in source:
            raise AssertionError('{$EMBY_DOMAIN} site is missing from gateway/Caddyfile')
        cls._tmpdir = tempfile.TemporaryDirectory(prefix='movie-emby-gw-')
        workdir = Path(cls._tmpdir.name)
        cert, key = make_self_signed(workdir)
        cls.movie_port = free_port()
        cls.emby_port = free_port()
        handler, records, lock = make_recording_stub()
        cls._records = records
        cls._lock = lock
        cls._stub = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        cls._thread = threading.Thread(target=cls._stub.serve_forever, daemon=True)
        cls._thread.start()
        upstream = '127.0.0.1:%d' % cls._stub.server_address[1]
        config = local_caddyfile(
            source,
            '127.0.0.1:%d' % cls.movie_port,
            upstream, cert, key,
            emby_site='127.0.0.1:%d' % cls.emby_port,
        )
        config_path = workdir / 'Caddyfile'
        config_path.write_text(config, encoding='utf-8')
        cls._caddy_cm = CaddyProcess(cls.caddy, config_path, cls.emby_port, workdir)
        cls._caddy_cm.__enter__()
        # movie port must also be up
        deadline = time.time() + 4
        while time.time() < deadline:
            try:
                with socket.create_connection(('127.0.0.1', cls.movie_port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.05)
        else:
            cls._caddy_cm.__exit__(None, None, None)
            raise TimeoutError('movie site did not listen')

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, '_caddy_cm', None) is not None:
            cls._caddy_cm.__exit__(None, None, None)
        if getattr(cls, '_stub', None) is not None:
            cls._stub.shutdown()
            cls._stub.server_close()
        if getattr(cls, '_thread', None) is not None:
            cls._thread.join(timeout=5)
        if getattr(cls, '_tmpdir', None) is not None:
            cls._tmpdir.cleanup()

    def setUp(self):
        with self._lock:
            self._records.clear()

    def _call(self, path, method='GET', headers=None, raw=False, port=None, body=None):
        port = self.emby_port if port is None else port
        return request_https(
            port, path, method=method, headers=headers, body=body,
            raw_path=path if raw else None,
        )

    def _stub_hits(self):
        with self._lock:
            return list(self._records)

    def _assert_no_store_and_single_referrer(self, headers, status_for_msg):
        self.assertEqual(
            cache_control_values(headers), [NO_STORE],
            'status=%s cc=%s' % (status_for_msg, cache_control_values(headers)))
        rp = header_values(headers, 'Referrer-Policy')
        self.assertEqual(rp, ['no-referrer'], 'Referrer-Policy=%s' % rp)

    def _assert_forbidden(self, path, method='GET', headers=None, raw=False):
        status, hdrs, body = self._call(path, method=method, headers=headers, raw=raw)
        self.assertEqual(status, 403, 'path=%r body=%r' % (path, body[:200]))
        self._assert_no_store_and_single_referrer(hdrs, status)
        self.assertEqual(self._stub_hits(), [], 'relay saw %s for %r' % (self._stub_hits(), path))
        return status, hdrs, body

    def _assert_not_found(self, path, method='GET', headers=None):
        status, hdrs, body = self._call(path, method=method, headers=headers)
        self.assertEqual(status, 404, 'path=%r body=%r' % (path, body[:200]))
        self._assert_no_store_and_single_referrer(hdrs, status)
        self.assertEqual(self._stub_hits(), [], 'relay saw %s for %r' % (self._stub_hits(), path))
        return status, hdrs, body

    def _assert_allowed(self, path, method='GET', headers=None):
        status, hdrs, body = self._call(path, method=method, headers=headers)
        self.assertEqual(status, 200, 'path=%r body=%r' % (path, body[:200]))
        self.assertTrue(body.startswith(b'UPSTREAM:'), body[:200])
        hits = self._stub_hits()
        self.assertEqual(len(hits), 1, hits)
        rp = header_values(hdrs, 'Referrer-Policy')
        self.assertEqual(rp, ['no-referrer'], 'Referrer-Policy=%s' % rp)
        return status, hdrs, body


class EmbyPublicLiveAllowTests(_EmbyLiveBase):
    def test_whitelist_paths_reach_relay(self):
        for name, method, path in ALLOW_CASES:
            with self.subTest(name=name, method=method, path=path):
                with self._lock:
                    self._records.clear()
                self._assert_allowed(path, method=method)

    def test_root_redirects_to_web(self):
        status, hdrs, body = self._call('/')
        self.assertIn(status, (301, 302, 307, 308), 'status=%s body=%r' % (status, body[:200]))
        location = hdrs.get('Location') or hdrs.get('location')
        self.assertIsNotNone(location)
        self.assertRegex(location, r'/web/')
        self._assert_no_store_and_single_referrer(hdrs, status)
        self.assertEqual(self._stub_hits(), [])

    def test_allowed_path_with_admin_token_still_reaches_relay(self):
        """Whitelist is path-based: a permitted path is still proxied even with an admin token."""
        self._assert_allowed('/Users/Me', headers=ADMIN_HEADERS)

    def test_allowed_path_with_api_key_query_reaches_relay_but_is_not_stored(self):
        status, hdrs, body = self._assert_allowed(
            '/Users/' + USER_ID + '/Items?api_key=ADMIN&X-Emby-Token=ADMIN')
        self.assertEqual(cache_control_values(hdrs), [NO_STORE])


class EmbyPublicLiveDenyTests(_EmbyLiveBase):
    def test_non_whitelist_apis_are_403_and_never_reach_relay(self):
        for name, method, path in DENY_403_CASES:
            with self.subTest(name=name, method=method, path=path):
                with self._lock:
                    self._records.clear()
                self._assert_forbidden(path, method=method)

    def test_admin_token_does_not_bypass_403(self):
        paths = [
            '/Users', '/Users/New', '/Users/' + USER_ID + '/Policy',
            '/Plugins', '/System/Info', '/ScheduledTasks',
            '/Library/Refresh', '/Library/VirtualFolders',
            '/Devices', '/Auth/Keys', '/Sessions', '/Encoding',
            '/emby/Plugins', '/mediabrowser/Users',
        ]
        for path in paths:
            with self.subTest(path=path):
                with self._lock:
                    self._records.clear()
                self._assert_forbidden(path, headers=ADMIN_HEADERS)
                with self._lock:
                    self._records.clear()
                self._assert_forbidden(path + '?api_key=ADMIN', headers=ADMIN_HEADERS)

    def test_management_pages_are_404_and_never_reach_relay(self):
        for path in DENY_404_CASES:
            with self.subTest(path=path):
                with self._lock:
                    self._records.clear()
                self._assert_not_found(path)
                with self._lock:
                    self._records.clear()
                self._assert_not_found(path, headers=ADMIN_HEADERS)

    def test_dangerous_raw_targets_are_rejected_and_never_reach_relay(self):
        for path in DANGEROUS_RAW:
            with self.subTest(path=path):
                with self._lock:
                    self._records.clear()
                status, hdrs, body = self._call(path, raw=True)
                self.assertIn(status, (400, 403), 'path=%r status=%s body=%r' % (path, status, body[:200]))
                self.assertEqual(self._stub_hits(), [], 'relay saw %s for raw %r' % (self._stub_hits(), path))
                if status == 403:
                    self._assert_no_store_and_single_referrer(hdrs, status)

    def test_websocket_upgrade_is_403(self):
        for path in ('/embywebsocket', '/emby/embywebsocket', '/Users/Me', '/socket'):
            with self.subTest(path=path):
                with self._lock:
                    self._records.clear()
                status, hdrs, body = self._call(path, headers={
                    'Connection': 'Upgrade',
                    'Upgrade': 'websocket',
                    'Sec-WebSocket-Version': '13',
                    'Sec-WebSocket-Key': 'dGhlIHNhbXBsZSBub25jZQ==',
                })
                self.assertNotEqual(status, 101, 'websocket upgraded for %r' % path)
                self.assertEqual(status, 403, 'path=%r status=%s body=%r' % (path, status, body[:200]))
                self._assert_no_store_and_single_referrer(hdrs, status)
                self.assertEqual(self._stub_hits(), [], self._stub_hits())


class EmbyPublicLiveCacheTests(_EmbyLiveBase):
    def test_versioned_web_static_without_auth_is_public(self):
        status, hdrs, body = self._assert_allowed('/web/main.js?v=4.10.2.0')
        values = cache_control_values(hdrs)
        self.assertEqual(len(values), 1, values)
        self.assertTrue(values[0].startswith(PUBLIC_STATIC_PREFIX), values)
        self.assertIn('max-age=', values[0])
        vary = ','.join(header_values(hdrs, 'Vary'))
        self.assertTrue(vary)

    def test_versioned_web_static_with_cookie_is_no_store(self):
        status, hdrs, body = self._assert_allowed(
            '/web/main.js?v=4.10.2.0', headers={'Cookie': 'sid=1'})
        self.assertEqual(cache_control_values(hdrs), [NO_STORE])

    def test_versioned_web_static_with_token_header_is_no_store(self):
        status, hdrs, body = self._assert_allowed(
            '/web/main.js?v=4.10.2.0', headers={'X-Emby-Token': 't'})
        self.assertEqual(cache_control_values(hdrs), [NO_STORE])

    def test_versioned_web_static_with_authorization_is_no_store(self):
        status, hdrs, body = self._assert_allowed(
            '/web/main.js?v=4.10.2.0',
            headers={'Authorization': 'MediaBrowser Token=t'})
        self.assertEqual(cache_control_values(hdrs), [NO_STORE])

    def test_versioned_web_static_with_api_key_query_is_no_store(self):
        status, hdrs, body = self._assert_allowed('/web/main.js?v=4.10.2.0&api_key=SECRET')
        self.assertEqual(cache_control_values(hdrs), [NO_STORE])

    def test_unversioned_web_js_is_no_store(self):
        status, hdrs, body = self._assert_allowed('/web/main.js')
        self.assertEqual(cache_control_values(hdrs), [NO_STORE])

    def test_html_even_with_version_is_no_store(self):
        status, hdrs, body = self._assert_allowed('/web/index.html?v=4.10.2.0')
        self.assertEqual(cache_control_values(hdrs), [NO_STORE])

    def test_anonymous_item_image_is_private_short_cache(self):
        status, hdrs, body = self._assert_allowed(
            '/Items/' + ITEM_ID + '/Images/Primary')
        self.assertEqual(cache_control_values(hdrs), [IMAGE_SHORT])
        vary = ','.join(header_values(hdrs, 'Vary'))
        self.assertRegex(vary, r'Authorization')
        self.assertRegex(vary, r'Cookie')
        self.assertRegex(vary, r'X-Emby-Token')

    def test_item_image_with_api_key_is_no_store(self):
        status, hdrs, body = self._assert_allowed(
            '/Items/' + ITEM_ID + '/Images/Primary?api_key=SECRET')
        self.assertEqual(cache_control_values(hdrs), [NO_STORE])

    def test_item_image_with_cookie_is_no_store(self):
        status, hdrs, body = self._assert_allowed(
            '/Items/' + ITEM_ID + '/Images/Primary', headers={'Cookie': 'sid=1'})
        self.assertEqual(cache_control_values(hdrs), [NO_STORE])

    def test_api_and_video_are_no_store(self):
        for path in (
                '/Users/Me',
                '/Users/' + USER_ID + '/Items',
                '/Items/' + ITEM_ID + '/PlaybackInfo',
                '/Videos/' + VIDEO_ID + '/stream',
                '/Videos/' + VIDEO_ID + '/master.m3u8',
                '/System/Info/Public',
                '/Sessions/Playing',
        ):
            with self.subTest(path=path):
                with self._lock:
                    self._records.clear()
                status, hdrs, body = self._assert_allowed(path, method='GET' if 'Playing' not in path else 'POST')
                self.assertEqual(cache_control_values(hdrs), [NO_STORE])

    def test_error_responses_are_no_store_including_403_and_404(self):
        for status_expected, path in ((403, '/Plugins'), (404, '/discover'), (403, '/Users')):
            with self.subTest(path=path):
                status, hdrs, body = self._call(path)
                self.assertEqual(status, status_expected)
                self._assert_no_store_and_single_referrer(hdrs, status)


class EmbyPublicLiveMovie2RegressionTests(_EmbyLiveBase):
    def test_movie2_discover_still_proxied(self):
        status, hdrs, body = self._call('/discover/ok', port=self.movie_port)
        self.assertEqual(status, 200, body[:200])
        self.assertTrue(body.startswith(b'UPSTREAM:'), body[:200])
        self.assertEqual(cache_control_values(hdrs), [NO_STORE])

    def test_movie2_referrer_policy_appears_once_when_upstream_sends_one(self):
        status, hdrs, body = self._call('/ok', port=self.movie_port)
        self.assertEqual(status, 200, body[:200])
        rp = header_values(hdrs, 'Referrer-Policy')
        self.assertEqual(rp, ['no-referrer'], 'expected a single no-referrer, got %s' % rp)

    def test_movie2_ge2o_still_404_with_no_store(self):
        status, hdrs, body = self._call('/ge2o', port=self.movie_port)
        self.assertEqual(status, 404)
        self.assertEqual(cache_control_values(hdrs), [NO_STORE])
        self.assertEqual(self._stub_hits(), [])

    def test_movie2_does_not_apply_emby_whitelist(self):
        """Management APIs on the original domain still reach relay (Access stays on movie2)."""
        status, hdrs, body = self._call('/Plugins', port=self.movie_port)
        self.assertEqual(status, 200, body[:200])
        self.assertTrue(body.startswith(b'UPSTREAM:'), body[:200])


class EmbyPublicLiveError502Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.caddy = find_caddy()
        if not cls.caddy:
            raise unittest.SkipTest(
                'caddy binary not found; set CADDY or add caddy to PATH')
        source = CADDYFILE.read_text(encoding='utf-8')
        if '{$EMBY_DOMAIN}' not in source:
            raise AssertionError('{$EMBY_DOMAIN} site is missing from gateway/Caddyfile')

    def test_allowed_path_502_has_exactly_one_no_store(self):
        source = CADDYFILE.read_text(encoding='utf-8')
        site_port = free_port()
        movie_port = free_port()
        dead = free_port()
        site = '127.0.0.1:%d' % site_port
        with tempfile.TemporaryDirectory(prefix='movie-emby-502-') as raw:
            workdir = Path(raw)
            cert, key = make_self_signed(workdir)
            config = local_caddyfile(
                source, '127.0.0.1:%d' % movie_port,
                '127.0.0.1:%d' % dead, cert, key, emby_site=site)
            config_path = workdir / 'Caddyfile'
            config_path.write_text(config, encoding='utf-8')
            with CaddyProcess(self.caddy, config_path, site_port, workdir):
                status, headers, body = request_https(site_port, '/System/Ping')
        self.assertEqual(status, 502, body[:200])
        self.assertEqual(cache_control_values(headers), [NO_STORE])
        self.assertEqual(header_values(headers, 'Referrer-Policy'), ['no-referrer'])



# --- MV-9 fix1: QA D1/D2/D3/D5/D4 assertions (written first; must fail on tree 904d7002) ---

WRITE_DENIED_CASES = [
    ('DELETE user by id', 'DELETE', '/Users/' + USER_ID),
    ('POST user by id', 'POST', '/Users/' + USER_ID),
    ('PUT user by id', 'PUT', '/Users/' + USER_ID),
    ('PATCH user by id', 'PATCH', '/Users/' + USER_ID),
    ('DELETE item by id', 'DELETE', '/Items/' + ITEM_ID),
    ('POST item by id', 'POST', '/Items/' + ITEM_ID),
    ('PUT item by id', 'PUT', '/Items/' + ITEM_ID),
    ('PATCH item by id', 'PATCH', '/Items/' + ITEM_ID),
    ('POST item image', 'POST', '/Items/' + ITEM_ID + '/Images/Primary'),
    ('DELETE item image', 'DELETE', '/Items/' + ITEM_ID + '/Images/Primary'),
    ('POST Items/Delete', 'POST', '/Items/Delete'),
    ('POST Videos/Delete', 'POST', '/Videos/' + VIDEO_ID + '/Delete'),
    ('DELETE Videos id', 'DELETE', '/Videos/' + VIDEO_ID),
    ('DELETE video stream', 'DELETE', '/Videos/' + VIDEO_ID + '/stream'),
    ('prefix emby DELETE user', 'DELETE', '/emby/Users/' + USER_ID),
    ('prefix mediabrowser POST Items/Delete', 'POST', '/mediabrowser/Items/Delete'),
]

ALLOWED_POST_CASES = [
    ('login AuthenticateByName', '/Users/AuthenticateByName'),
    ('PlaybackInfo', '/Items/' + ITEM_ID + '/PlaybackInfo'),
    ('Sessions/Playing', '/Sessions/Playing'),
    ('Sessions/Playing/Progress', '/Sessions/Playing/Progress'),
    ('Sessions/Playing/Stopped', '/Sessions/Playing/Stopped'),
    ('Sessions/Playing/Ping', '/Sessions/Playing/Ping'),
    ('prefix emby login', '/emby/Users/AuthenticateByName'),
    ('prefix mediabrowser PlaybackInfo', '/mediabrowser/Items/' + ITEM_ID + '/PlaybackInfo'),
]

DOUBLE_ENCODED_RAW = [
    '/web/%252e%252e/Plugins',
    '/web/%252E%252E/Plugins',
    '/web/%252e%252e%252fPlugins',
    '/emby/%252e%252e/Plugins',
    '/Users/Me%252fPolicy',
    '/web/%25%32%65%25%32%65/Plugins',
    '/web/%252f%252e%252e/x',
    '/foo/%255c%255c/x',
    '/web/%252E%252e/%252FPlugins',
]

WS_HEADERS = {
    'Connection': 'Upgrade',
    'Upgrade': 'websocket',
    'Sec-WebSocket-Version': '13',
    'Sec-WebSocket-Key': 'dGhlIHNhbXBsZSBub25jZQ==',
}


class EmbyPublicLiveFix1DenyTests(_EmbyLiveBase):
    """QA D1/D2/D3/D5: 403/4xx must be no-store; write methods 403; double encoding blocked."""

    def test_d1_double_slash_versioned_js_403_is_no_store_not_public(self):
        # QA C17: GET /web//apploader.js?v=… is gateway 403, must not be public immutable.
        status, hdrs, body = self._call('/web//apploader.js?v=4.10.2.0', raw=True)
        self.assertEqual(status, 403, 'body=%r' % body[:200])
        cc = cache_control_values(hdrs)
        self.assertEqual(cc, [NO_STORE], 'D1: 403 carried %s' % cc)
        self.assertFalse(any('public' in v.lower() for v in cc), cc)
        self.assertEqual(self._stub_hits(), [])
        self._assert_no_store_and_single_referrer(hdrs, status)

    def test_d1_websocket_on_versioned_js_403_is_no_store_not_public(self):
        # QA W08
        status, hdrs, body = self._call('/web/apploader.js?v=4.10.2.0', headers=WS_HEADERS)
        self.assertNotEqual(status, 101)
        self.assertEqual(status, 403, 'body=%r' % body[:200])
        cc = cache_control_values(hdrs)
        self.assertEqual(cc, [NO_STORE], 'D1: WS 403 carried %s' % cc)
        self.assertFalse(any('public' in v.lower() for v in cc), cc)
        self.assertEqual(self._stub_hits(), [])

    def test_d2_websocket_on_image_403_is_no_store(self):
        # QA W09
        status, hdrs, body = self._call(
            '/Items/' + ITEM_ID + '/Images/Primary', headers=WS_HEADERS)
        self.assertEqual(status, 403, 'body=%r' % body[:200])
        cc = cache_control_values(hdrs)
        self.assertEqual(cc, [NO_STORE], 'D2: image WS 403 carried %s' % cc)
        self.assertNotIn('max-age=300', ''.join(cc))
        self.assertEqual(self._stub_hits(), [])

    def test_d3_named_write_methods_are_403_and_never_reach_relay(self):
        for name, method, path in WRITE_DENIED_CASES:
            with self.subTest(name=name, method=method, path=path):
                with self._lock:
                    self._records.clear()
                self._assert_forbidden(path, method=method)
                with self._lock:
                    self._records.clear()
                self._assert_forbidden(path, method=method, headers=ADMIN_HEADERS)

    def test_d3_allowed_posts_reach_upstream(self):
        for name, path in ALLOWED_POST_CASES:
            with self.subTest(name=name, path=path):
                with self._lock:
                    self._records.clear()
                status, hdrs, body = self._assert_allowed(path, method='POST')
                self.assertEqual(cache_control_values(hdrs), [NO_STORE])

    def test_d3_get_delete_named_paths_are_403(self):
        """Items/Delete and Videos/.../Delete must not be treated as an item id / stream subpath."""
        for path in ('/Items/Delete', '/Videos/' + VIDEO_ID + '/Delete',
                     '/emby/Items/Delete'):
            with self.subTest(path=path):
                with self._lock:
                    self._records.clear()
                self._assert_forbidden(path, method='GET')
                with self._lock:
                    self._records.clear()
                self._assert_forbidden(path, method='POST')

    def test_d5_double_encoding_is_blocked_and_never_reaches_relay(self):
        for path in DOUBLE_ENCODED_RAW:
            with self.subTest(path=path):
                with self._lock:
                    self._records.clear()
                status, hdrs, body = self._call(path, raw=True)
                self.assertIn(status, (400, 403),
                              'D5: path=%r status=%s body=%r hits=%s' % (
                                  path, status, body[:200], self._stub_hits()))
                self.assertEqual(self._stub_hits(), [],
                                 'D5: relay saw %s for %r' % (self._stub_hits(), path))
                if status == 403:
                    self._assert_no_store_and_single_referrer(hdrs, status)
                    cc = cache_control_values(hdrs)
                    self.assertFalse(any('public' in v.lower() for v in cc), cc)


class EmbyPublicLiveFix1OpenlistTests(_EmbyLiveBase):
    """QA D4: /openlist/d with sign is GET/HEAD only; everything else 403; no-store."""

    def test_d4_openlist_d_with_sign_reaches_upstream_no_store(self):
        path = '/openlist/d/aliyun/Movies/MV4-Test-Long/MV4-Test-Long.mkv?sign=test-sign'
        for method in ('GET', 'HEAD'):
            with self.subTest(method=method):
                with self._lock:
                    self._records.clear()
                status, hdrs, body = self._call(path, method=method)
                self.assertEqual(status, 200, 'body=%r hits=%s' % (body[:200], self._stub_hits()))
                if method == 'GET':
                    self.assertTrue(body.startswith(b'UPSTREAM:'), body[:200])
                self.assertEqual(len(self._stub_hits()), 1, self._stub_hits())
                self.assertEqual(cache_control_values(hdrs), [NO_STORE])
                rp = header_values(hdrs, 'Referrer-Policy')
                self.assertEqual(rp, ['no-referrer'], rp)

    def test_d4_openlist_d_without_or_empty_sign_is_403(self):
        for path in (
                '/openlist/d/aliyun/Movies/MV4-Test-Long/MV4-Test-Long.mkv',
                '/openlist/d/aliyun/Movies/MV4-Test-Long/MV4-Test-Long.mkv?sign=',
                '/openlist/d/aliyun/Movies/x.mkv?foo=1',
        ):
            with self.subTest(path=path):
                with self._lock:
                    self._records.clear()
                self._assert_forbidden(path, method='GET')

    def test_d4_openlist_other_paths_and_methods_are_403(self):
        cases = [
            ('GET', '/openlist'),
            ('GET', '/openlist/'),
            ('GET', '/openlist/api'),
            ('GET', '/openlist/api/fs/list'),
            ('GET', '/openlist/p/aliyun/Movies/x.mkv?sign=test-sign'),
            ('POST', '/openlist/d/aliyun/Movies/x.mkv?sign=test-sign'),
            ('PUT', '/openlist/d/aliyun/Movies/x.mkv?sign=test-sign'),
            ('DELETE', '/openlist/d/aliyun/Movies/x.mkv?sign=test-sign'),
            ('GET', '/OPENLIST/api'),
        ]
        for method, path in cases:
            with self.subTest(method=method, path=path):
                with self._lock:
                    self._records.clear()
                self._assert_forbidden(path, method=method)


class _ImageErrorStub(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def do_GET(self):
        body = b'bad image'
        self.send_response(400)
        self.send_header('Cache-Control', 'public, max-age=999')
        self.send_header('Content-Type', 'text/plain')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


class EmbyPublicLiveFix1ImageUpstreamErrorTests(unittest.TestCase):
    """QA D2 C10: upstream 4xx on an image path must be no-store, not private max-age=300."""

    @classmethod
    def setUpClass(cls):
        cls.caddy = find_caddy()
        if not cls.caddy:
            raise unittest.SkipTest(
                'caddy binary not found; set CADDY or add caddy to PATH')
        source = CADDYFILE.read_text(encoding='utf-8')
        if '{$EMBY_DOMAIN}' not in source:
            raise AssertionError('{$EMBY_DOMAIN} site is missing from gateway/Caddyfile')
        cls._tmpdir = tempfile.TemporaryDirectory(prefix='movie-emby-img4xx-')
        workdir = Path(cls._tmpdir.name)
        cert, key = make_self_signed(workdir)
        cls.movie_port = free_port()
        cls.emby_port = free_port()
        cls._stub = ThreadingHTTPServer(('127.0.0.1', 0), _ImageErrorStub)
        cls._thread = threading.Thread(target=cls._stub.serve_forever, daemon=True)
        cls._thread.start()
        upstream = '127.0.0.1:%d' % cls._stub.server_address[1]
        config = local_caddyfile(
            source,
            '127.0.0.1:%d' % cls.movie_port,
            upstream, cert, key,
            emby_site='127.0.0.1:%d' % cls.emby_port,
        )
        config_path = workdir / 'Caddyfile'
        config_path.write_text(config, encoding='utf-8')
        cls._caddy_cm = CaddyProcess(cls.caddy, config_path, cls.emby_port, workdir)
        cls._caddy_cm.__enter__()

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, '_caddy_cm', None) is not None:
            cls._caddy_cm.__exit__(None, None, None)
        if getattr(cls, '_stub', None) is not None:
            cls._stub.shutdown()
            cls._stub.server_close()
        if getattr(cls, '_thread', None) is not None:
            cls._thread.join(timeout=5)
        if getattr(cls, '_tmpdir', None) is not None:
            cls._tmpdir.cleanup()

    def test_d2_upstream_400_on_image_is_no_store(self):
        status, hdrs, body = request_https(
            self.emby_port, '/Items/' + ITEM_ID + '/Images/Primary')
        self.assertEqual(status, 400, body[:200])
        cc = cache_control_values(hdrs)
        self.assertEqual(cc, [NO_STORE], 'D2: upstream 400 carried %s' % cc)
        self.assertNotIn('max-age=300', ''.join(cc))
        self.assertFalse(any('public' in v.lower() for v in cc), cc)
        rp = header_values(hdrs, 'Referrer-Policy')
        self.assertEqual(rp, ['no-referrer'], rp)


class _RedirectStub(BaseHTTPRequestHandler):
    """Simulate relay's 307 to movie2 /openlist/p with a sign, or OpenList 401."""
    protocol_version = 'HTTP/1.1'

    def do_GET(self):
        self._go()

    def do_HEAD(self):
        self._go(write_body=False)

    def do_POST(self):
        self._go()

    def _go(self, write_body=True):
        path = self.path
        if path.lower().startswith('/openlist/'):
            # Missing/bad sign is the gateway's job; if a request arrives here
            # without being a valid /d/ proxy we still answer 401 like OpenList.
            qs = path.split('?', 1)
            q = qs[1] if len(qs) > 1 else ''
            if 'sign=good' in q and '/openlist/d/' in path.lower():
                data = b'OL-OK'
                self.send_response(206)
                self.send_header('Content-Range', 'bytes 0-5/100')
                self.send_header('Content-Type', 'video/x-matroska')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                if write_body:
                    self.wfile.write(data)
                return
            data = b'unauthorized'
            self.send_response(401)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            if write_body:
                self.wfile.write(data)
            return
        if '/stream' in path.lower() or path.lower().startswith('/videos/'):
            loc = 'https://movie2.g60.net/openlist/p/aliyun/Movies/X.mkv?sign=good'
            body = b'redirect'
            self.send_response(307)
            self.send_header('Location', loc)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            if write_body:
                self.wfile.write(body)
            return
        data = b'UPSTREAM-OK'
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        if write_body:
            self.wfile.write(data)

    def log_message(self, format, *args):
        pass


class EmbyPublicLiveFix1RedirectTests(unittest.TestCase):
    """QA D4: Location from relay is rewritten off movie2 onto this site's /openlist/d."""

    @classmethod
    def setUpClass(cls):
        cls.caddy = find_caddy()
        if not cls.caddy:
            raise unittest.SkipTest(
                'caddy binary not found; set CADDY or add caddy to PATH')
        source = CADDYFILE.read_text(encoding='utf-8')
        if '{$EMBY_DOMAIN}' not in source:
            raise AssertionError('{$EMBY_DOMAIN} site is missing from gateway/Caddyfile')
        cls._tmpdir = tempfile.TemporaryDirectory(prefix='movie-emby-redir-')
        workdir = Path(cls._tmpdir.name)
        cert, key = make_self_signed(workdir)
        cls.movie_port = free_port()
        cls.emby_port = free_port()
        cls._stub = ThreadingHTTPServer(('127.0.0.1', 0), _RedirectStub)
        cls._thread = threading.Thread(target=cls._stub.serve_forever, daemon=True)
        cls._thread.start()
        upstream = '127.0.0.1:%d' % cls._stub.server_address[1]
        config = local_caddyfile(
            source,
            '127.0.0.1:%d' % cls.movie_port,
            upstream, cert, key,
            emby_site='127.0.0.1:%d' % cls.emby_port,
        )
        config_path = workdir / 'Caddyfile'
        config_path.write_text(config, encoding='utf-8')
        cls._caddy_cm = CaddyProcess(cls.caddy, config_path, cls.emby_port, workdir)
        cls._caddy_cm.__enter__()
        deadline = time.time() + 4
        while time.time() < deadline:
            try:
                with socket.create_connection(('127.0.0.1', cls.movie_port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.05)
        else:
            cls._caddy_cm.__exit__(None, None, None)
            raise TimeoutError('movie site did not listen')

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, '_caddy_cm', None) is not None:
            cls._caddy_cm.__exit__(None, None, None)
        if getattr(cls, '_stub', None) is not None:
            cls._stub.shutdown()
            cls._stub.server_close()
        if getattr(cls, '_thread', None) is not None:
            cls._thread.join(timeout=5)
        if getattr(cls, '_tmpdir', None) is not None:
            cls._tmpdir.cleanup()

    def test_d4_stream_location_leaves_movie2_and_uses_openlist_d(self):
        status, hdrs, body = request_https(
            self.emby_port, '/Videos/' + VIDEO_ID + '/stream?static=true')
        self.assertEqual(status, 307, body[:200])
        locs = header_values(hdrs, 'Location')
        self.assertEqual(len(locs), 1, locs)
        loc = locs[0]
        self.assertNotIn('movie2', loc.lower(), 'D4: Location still mentions movie2: %s' % loc)
        self.assertNotIn('g60.net', loc.lower(), loc)
        # relative or same-host /openlist/d/…?sign=
        self.assertRegex(loc, r'(?i)(/openlist/d/)')
        self.assertNotRegex(loc, r'(?i)/openlist/p/')
        self.assertIn('sign=good', loc)
        self.assertEqual(cache_control_values(hdrs), [NO_STORE])

    def test_d4_movie2_site_does_not_rewrite_location(self):
        """Admin domain playback redirects stay as the upstream sent them."""
        status, hdrs, body = request_https(
            self.movie_port, '/Videos/' + VIDEO_ID + '/stream?static=true')
        self.assertEqual(status, 307, body[:200])
        locs = header_values(hdrs, 'Location')
        self.assertEqual(len(locs), 1, locs)
        self.assertIn('movie2.g60.net', locs[0])
        self.assertIn('/openlist/p/', locs[0])

    def test_d4_openlist_401_from_upstream_becomes_gateway_403(self):
        # Tampered/wrong sign reaches OpenList as 401; public site must surface 403.
        status, hdrs, body = request_https(
            self.emby_port,
            '/openlist/d/aliyun/Movies/X.mkv?sign=tampered')
        self.assertEqual(status, 403, 'body=%r' % body[:200])
        self.assertEqual(body.strip(), b'Forbidden')
        self.assertEqual(cache_control_values(hdrs), [NO_STORE])



if __name__ == '__main__':
    unittest.main()
