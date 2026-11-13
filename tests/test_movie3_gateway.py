"""MV-9b movie3 public site: prefix boundaries, dual entry, discover, cookies.

These tests are written first (TDD). They fail on the fix2 EMBY_DOMAIN tree
and pass once gateway/Caddyfile serves {$MOVIE3_DOMAIN} as specified by PM.
movie2 behaviour is covered by the existing tests in test_gateway.py.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import os
import tempfile
import threading
import time
import unittest

try:
    from test_gateway import (
    ALLOW_CASES,
    ALLOWED_POST_CASES,
    CADDYFILE,
    DANGEROUS_RAW,
    DOUBLE_ENCODED_RAW,
    ITEM_ID,
    NO_STORE,
    SETUP,
    USER_ID,
    VIDEO_ID,
    WRITE_DENIED_CASES,
    WS_HEADERS,
    ADMIN_HEADERS,
    CaddyProcess,
    PUBLIC_STATIC_PREFIX,
    cache_control_values,
    find_caddy,
    free_port,
    handle_block,
    header_values,
    local_caddyfile,
    make_self_signed,
    public_movie_path,
    public_site_placeholder,
    request_https,
    IMAGE_SHORT,
    _EmbyLiveBase,
    _RedirectStub,
)
except ImportError:
    from tests.test_gateway import (
        ALLOW_CASES,
        ALLOWED_POST_CASES,
        CADDYFILE,
        DANGEROUS_RAW,
        DOUBLE_ENCODED_RAW,
        ITEM_ID,
        NO_STORE,
        SETUP,
        USER_ID,
        VIDEO_ID,
        WRITE_DENIED_CASES,
        WS_HEADERS,
        ADMIN_HEADERS,
        CaddyProcess,
        PUBLIC_STATIC_PREFIX,
        cache_control_values,
        find_caddy,
        free_port,
        handle_block,
        header_values,
        local_caddyfile,
        make_self_signed,
        public_movie_path,
        public_site_placeholder,
        request_https,
        IMAGE_SHORT,
        _EmbyLiveBase,
        _RedirectStub,
    )


# Management APIs on portal (portal/server.py handle_request). All of these
# either issue a cinema session, require one, or are the cinema_admin probe.
# On movie3 they must be gateway 403 regardless of Cookie.
DISCOVER_ADMIN_APIS = [
    ('POST', '/discover/api/login'),
    ('GET', '/discover/api/session'),
    ('GET', '/discover/api/admin-check'),
    ('GET', '/discover/api/metrics'),
    ('GET', '/discover/api/status'),
    ('GET', '/discover/api/search?q=x&kind=movie'),
    ('GET', '/discover/api/recommendations'),
    ('GET', '/discover/api/series'),
    ('GET', '/discover/api/tasks'),
    ('GET', '/discover/api/playbacks'),
    ('POST', '/discover/api/logout'),
    ('POST', '/discover/api/settings'),
    ('POST', '/discover/api/configure'),
    ('POST', '/discover/api/preview-version'),
    ('POST', '/discover/api/download-release'),
    ('POST', '/discover/api/follow'),
    ('POST', '/discover/api/episodes'),
    ('POST', '/discover/api/download-episodes'),
    ('POST', '/discover/api/retry-upload'),
    ('POST', '/discover/api/task-action'),
    ('POST', '/discover/api/watch-session'),
    ('POST', '/discover/api/metadata-defaults'),
    ('POST', '/discover/api/remove-series'),
    ('POST', '/discover/api/monitor'),
    ('POST', '/discover/api/download'),
    ('GET', '/discover/api/'),
    ('GET', '/discover/api/unknown'),
]

DISCOVER_STATIC = [
    '/discover/',
    '/discover/index.html',
    '/discover/app.js',
    '/discover/theme.js',
    '/discover/paint.js',
    '/discover/style.css',
]

LOOKALIKE_PATHS = [
    '/movies',
    '/movies/',
    '/movies/web/index.html',
    '/movie2',
    '/movie2/',
    '/movie2/web/index.html',
    '/movieX',
    '/movieX/web/index.html',
    '/movie_backup',
    '/movie3',
]


def emby_root_path(path):
    """Place a bare Emby path under the root /emby client prefix."""
    if not path or path == '/':
        return '/emby'
    lower = path.lower()
    if lower.startswith('/emby') or lower.startswith('/mediabrowser') or lower.startswith('/embywebsocket'):
        return path
    if lower.startswith('/openlist') or lower.startswith('/discover') or lower.startswith('/movie'):
        return path
    if path.startswith('/'):
        return '/emby' + path
    return '/emby/' + path


class Movie3DocTests(unittest.TestCase):
    def setUp(self):
        self.config = CADDYFILE.read_text(encoding='utf-8')

    def test_movie3_site_uses_handle_path_and_root_client_paths(self):
        block = handle_block(self.config, '{$MOVIE3_DOMAIN}')
        self.assertIn('handle_path /movie/*', block)
        self.assertIn('/embywebsocket', block)
        # @dangerous must see the original URI (site-level or otherwise raw).
        self.assertIn("{http.request.uri}.matches", block)
        self.assertIn('%252e', self.config + SETUP.read_text(encoding='utf-8'))
        # movie2 site is unchanged: no /movie handle_path there.
        movie = handle_block(self.config, '{$MOVIE_DOMAIN}')
        self.assertNotIn('handle_path /movie/*', movie)

    def test_compose_passes_movie3_domain(self):
        compose = (Path(__file__).resolve().parents[1] / 'compose.yaml').read_text(encoding='utf-8')
        self.assertIn('MOVIE3_DOMAIN', compose)
        self.assertIn('MOVIE_DOMAIN', compose)


class Movie3PrefixLiveTests(_EmbyLiveBase):
    """Prefix boundaries, dual D1–D6 entry, /web bounce, websocket allow."""

    def test_lookalike_paths_never_reach_emby(self):
        for path in LOOKALIKE_PATHS:
            with self.subTest(path=path):
                with self._lock:
                    self._records.clear()
                status, hdrs, body = self._call(path)
                self.assertNotIn(status, (200, 206, 301, 302, 307, 308),
                                 'lookalike %r reached a success/redirect status=%s' % (path, status))
                self.assertIn(status, (403, 404), 'path=%r status=%s body=%r' % (path, status, body[:200]))
                self.assertEqual(self._stub_hits(), [], 'lookalike %r hit upstream %s' % (path, self._stub_hits()))
                self._assert_no_store_and_single_referrer(hdrs, status)

    def test_encoded_traversals_are_403(self):
        raws = [
            '/movie/%2e%2e/discover/',
            '/movie/%2e%2e/',
            '/movie/%252e%252e/',
            '/movie%2fweb/index.html',
            '//movie/',
            '//movie/web/index.html',
            '/movie//web/index.html',
            '/movie/%2fdiscover/',
            '/emby/%2e%2e/Users/Me',
            '/emby/%252e%252e/Plugins',
            '/movie/web/%252e%252e/Plugins',
            '/movie/%25%32%65%25%32%65/Plugins',
        ]
        for path in raws:
            with self.subTest(path=path):
                with self._lock:
                    self._records.clear()
                status, hdrs, body = self._call(path, raw=True)
                self.assertIn(status, (400, 403),
                              'path=%r status=%s body=%r hits=%s' % (
                                  path, status, body[:200], self._stub_hits()))
                self.assertEqual(self._stub_hits(), [], 'relay saw %s for %r' % (self._stub_hits(), path))
                if status == 403:
                    self._assert_no_store_and_single_referrer(hdrs, status)

    def test_literal_dotdot_under_movie_matches_direct_discover(self):
        """Caddy cleans /movie/../discover/ to /discover/; same movie3 rules, no bypass."""
        with self._lock:
            self._records.clear()
        status_direct, hdrs_direct, body_direct = self._call('/discover/')
        with self._lock:
            self._records.clear()
        # Send literal '..' on the wire (raw request-target). Matching uses
        # the cleaned path, but the upstream URI must also be the cleaned
        # /discover/ — same bytes as a direct visit, not a portal 404.
        status_dot, hdrs_dot, body_dot = self._call('/movie/../discover/', raw=True)
        self.assertEqual(status_dot, status_direct,
                         'dot-dot status=%s direct=%s body=%r' % (
                             status_dot, status_direct, body_dot[:200]))
        self.assertEqual(cache_control_values(hdrs_dot), cache_control_values(hdrs_direct))
        # Both are movie3 /discover: static 200, never a cinema_admin 302,
        # and the cleaned request is not treated as an Emby /movie path.
        self.assertEqual(status_direct, 200, body_direct[:200])
        self.assertTrue(body_dot.startswith(b'UPSTREAM:'), body_dot[:200])
        self.assertTrue(body_direct.startswith(b'UPSTREAM:'), body_direct[:200])
        self.assertEqual(body_dot, body_direct,
                         'upstream URI must equal direct /discover/; dot=%r direct=%r' % (
                             body_dot, body_direct))
        self.assertIn(b':/discover/', body_dot)

    def test_movie_exact_redirects_to_slash_with_empty_body(self):
        status, hdrs, body = self._call('/movie')
        self.assertEqual(status, 308, 'status=%s body=%r' % (status, body[:200]))
        locs = header_values(hdrs, 'Location')
        self.assertEqual(len(locs), 1, locs)
        self.assertRegex(locs[0], r'(?i)^/movie/$')
        self.assertFalse(locs[0].lower().startswith('http'))
        self.assertEqual(body, b'')
        self.assertEqual(header_values(hdrs, 'Content-Length'), ['0'])
        self._assert_no_store_and_single_referrer(hdrs, status)
        self.assertEqual(self._stub_hits(), [])

    def test_movie_slash_redirects_to_web_index(self):
        status, hdrs, body = self._call('/movie/')
        self.assertIn(status, (301, 302, 307, 308), 'status=%s' % status)
        locs = header_values(hdrs, 'Location')
        self.assertEqual(len(locs), 1, locs)
        self.assertRegex(locs[0], r'(?i)^/movie/web/index\.html$')
        self.assertEqual(body, b'')
        self.assertEqual(header_values(hdrs, 'Content-Length'), ['0'])
        self.assertEqual(self._stub_hits(), [])

    def test_root_web_redirects_back_to_movie(self):
        # Must hit the domain root /web, not the auto-prefixed /movie/web.
        for path in ('/web/', '/web/index.html', '/web/main.js?v=4.10.2.0',
                     '/web/dashboard'):
            with self.subTest(path=path):
                with self._lock:
                    self._records.clear()
                status, hdrs, body = request_https(self.emby_port, path)
                self.assertIn(status, (301, 302, 307, 308),
                              'path=%r status=%s body=%r' % (path, status, body[:200]))
                locs = header_values(hdrs, 'Location')
                self.assertEqual(len(locs), 1, locs)
                self.assertRegex(locs[0], r'(?i)^/movie' + path.replace('?', r'\?'))
                self.assertFalse(locs[0].lower().startswith('http'))
                self.assertNotIn('movie2', locs[0].lower())
                self.assertEqual(body, b'')
                self.assertEqual(header_values(hdrs, 'Content-Length'), ['0'])
                self._assert_no_store_and_single_referrer(hdrs, status)
                self.assertEqual(self._stub_hits(), [])

    def test_d1_d6_whitelist_on_emby_root_prefix(self):
        for name, method, path in ALLOW_CASES:
            rooted = emby_root_path(path)
            if rooted.startswith('/movie') or rooted.startswith('/openlist'):
                continue
            with self.subTest(name=name, path=rooted, method=method):
                with self._lock:
                    self._records.clear()
                self._assert_allowed(rooted, method=method)

    def test_d3_allowed_posts_on_emby_root_prefix(self):
        for name, path in ALLOWED_POST_CASES:
            rooted = emby_root_path(path)
            with self.subTest(name=name, path=rooted):
                with self._lock:
                    self._records.clear()
                status, hdrs, body = self._assert_allowed(rooted, method='POST')
                self.assertEqual(cache_control_values(hdrs), [NO_STORE])

    def test_d3_write_methods_403_on_both_entries(self):
        for name, method, path in WRITE_DENIED_CASES:
            for labelled in (public_movie_path(path), emby_root_path(path)):
                with self.subTest(name=name, method=method, path=labelled):
                    with self._lock:
                        self._records.clear()
                    self._assert_forbidden(labelled, method=method, headers=ADMIN_HEADERS)

    def test_admin_token_denied_on_both_entries(self):
        for path in ('/Plugins', '/Users', '/System/Logs', '/Sessions'):
            for labelled in (public_movie_path(path), emby_root_path(path)):
                with self.subTest(path=labelled):
                    with self._lock:
                        self._records.clear()
                    self._assert_forbidden(labelled, headers=ADMIN_HEADERS)

    def test_d5_double_encoding_on_both_entries(self):
        extras = ['/movie' + p if not p.lower().startswith('/movie') else p for p in DOUBLE_ENCODED_RAW]
        extras += [emby_root_path(p) if not p.lower().startswith('/emby') else p for p in DOUBLE_ENCODED_RAW]
        seen = set()
        for path in extras:
            if path in seen:
                continue
            seen.add(path)
            with self.subTest(path=path):
                with self._lock:
                    self._records.clear()
                status, hdrs, body = self._call(path, raw=True)
                self.assertIn(status, (400, 403),
                              'D5 path=%r status=%s hits=%s' % (path, status, self._stub_hits()))
                self.assertEqual(self._stub_hits(), [])
                if status == 403:
                    self._assert_no_store_and_single_referrer(hdrs, status)

    def test_dangerous_raw_on_both_entries(self):
        for path in DANGEROUS_RAW:
            for labelled in (public_movie_path(path), emby_root_path(path)):
                with self.subTest(path=labelled):
                    with self._lock:
                        self._records.clear()
                    status, hdrs, body = self._call(labelled, raw=True)
                    self.assertIn(status, (400, 403), 'path=%r status=%s' % (labelled, status))
                    self.assertEqual(self._stub_hits(), [])

    def test_embywebsocket_reaches_upstream_on_root_and_movie(self):
        for path in ('/embywebsocket', '/movie/embywebsocket', '/emby/embywebsocket'):
            with self.subTest(path=path):
                with self._lock:
                    self._records.clear()
                status, hdrs, body = self._call(path, headers=WS_HEADERS)
                self.assertEqual(status, 200, 'path=%r status=%s body=%r hits=%s' % (
                    path, status, body[:200], self._stub_hits()))
                self.assertTrue(body.startswith(b'UPSTREAM:'), body[:200])
                self.assertEqual(len(self._stub_hits()), 1, self._stub_hits())
                self.assertEqual(cache_control_values(hdrs), [NO_STORE])

    def test_websocket_on_other_paths_still_403_both_entries(self):
        for path in (public_movie_path('/web/apploader.js?v=4.10.2.0'),
                     public_movie_path('/Items/' + ITEM_ID + '/Images/Primary'),
                     emby_root_path('/Users/Me')):
            with self.subTest(path=path):
                with self._lock:
                    self._records.clear()
                status, hdrs, body = self._call(path, headers=WS_HEADERS)
                self.assertEqual(status, 403, 'path=%r status=%s' % (path, status))
                self.assertEqual(self._stub_hits(), [])
                self._assert_no_store_and_single_referrer(hdrs, status)

    def test_versioned_static_and_images_under_movie_and_emby(self):
        status, hdrs, body = self._assert_allowed('/movie/web/main.js?v=4.10.2.0')
        values = cache_control_values(hdrs)
        self.assertTrue(values[0].startswith(PUBLIC_STATIC_PREFIX), values)
        with self._lock:
            self._records.clear()
        status, hdrs, body = self._assert_allowed(
            '/movie/web/main.js?v=4.10.2.0', headers={'X-Emby-Token': 't'})
        self.assertEqual(cache_control_values(hdrs), [NO_STORE])
        with self._lock:
            self._records.clear()
        status, hdrs, body = self._assert_allowed('/movie/Items/' + ITEM_ID + '/Images/Primary')
        self.assertEqual(cache_control_values(hdrs), [IMAGE_SHORT])
        with self._lock:
            self._records.clear()
        status, hdrs, body = self._assert_allowed('/emby/Items/' + ITEM_ID + '/Images/Primary')
        self.assertEqual(cache_control_values(hdrs), [IMAGE_SHORT])

    def test_management_pages_404_including_under_movie(self):
        for path in ('/search', '/search/', '/sonarr', '/qb', '/ge2o',
                     '/openlist', '/openlist/', '/openlist/api',
                     '/movie/search/', '/movie/sonarr/', '/movie/qb/',
                     '/movie/web/dashboard', '/emby/web/dashboard',
                     '/emby/search/', '/mediabrowser/sonarr/'):
            with self.subTest(path=path):
                with self._lock:
                    self._records.clear()
                self._assert_not_found(path, headers=ADMIN_HEADERS)


class Movie3DiscoverLiveTests(_EmbyLiveBase):
    """ /discover is on movie3 but management APIs and cinema cookies are not. """

    def test_discover_static_reaches_portal(self):
        for path in DISCOVER_STATIC:
            with self.subTest(path=path):
                with self._lock:
                    self._records.clear()
                status, hdrs, body = self._call(path)
                self.assertEqual(status, 200, 'path=%r body=%r' % (path, body[:200]))
                self.assertTrue(body.startswith(b'UPSTREAM:'), body[:200])
                self.assertEqual(len(self._stub_hits()), 1, self._stub_hits())
                self.assertEqual(cache_control_values(hdrs), [NO_STORE])

    def test_discover_admin_apis_are_403_even_with_cinema_cookie(self):
        cinema = {'Cookie': 'cinema=forged-admin-session; other=keep',
                  'Origin': 'https://127.0.0.1',
                  'Content-Type': 'application/json',
                  'X-CSRF-Token': 'forged'}
        for method, path in DISCOVER_ADMIN_APIS:
            with self.subTest(method=method, path=path):
                with self._lock:
                    self._records.clear()
                body = b'{"x":1}' if method == 'POST' else None
                status, hdrs, body_out = self._call(
                    path, method=method, headers=cinema, body=body)
                self.assertEqual(status, 403,
                                 'path=%r method=%s status=%s body=%r' % (
                                     path, method, status, body_out[:200]))
                self.assertEqual(self._stub_hits(), [],
                                 'portal saw %s for %r' % (self._stub_hits(), path))
                self._assert_no_store_and_single_referrer(hdrs, status)
                # Response must not set a cinema cookie.
                for value in header_values(hdrs, 'Set-Cookie'):
                    self.assertFalse(value.lower().startswith('cinema='),
                                     'Set-Cookie leaked cinema: %s' % value)


class _CookieEchoStub(BaseHTTPRequestHandler):
    """Portal-like stub: echoes Cookie and always tries to set cinema=."""
    protocol_version = 'HTTP/1.1'
    records = None
    lock = None

    def do_GET(self):
        self._go()

    def do_POST(self):
        self._go()

    def do_HEAD(self):
        self._go(write_body=False)

    def _go(self, write_body=True):
        length = int(self.headers.get('Content-Length') or 0)
        if length:
            self.rfile.read(length)
        cookie = self.headers.get('Cookie') or ''
        with self.lock:
            self.records.append({'path': self.path, 'cookie': cookie, 'method': self.command})
        data = ('ECHO-COOKIE:' + cookie).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Set-Cookie', 'cinema=secret-sid; Path=/; HttpOnly; SameSite=Strict')
        self.send_header('Set-Cookie', 'unrelated=ok; Path=/')
        self.send_header('Cache-Control', 'public, max-age=9')
        self.end_headers()
        if write_body:
            self.wfile.write(data)

    def log_message(self, format, *args):
        pass


class Movie3CinemaCookieLiveTests(unittest.TestCase):
    """movie3 must strip cinema cookies in both directions on /discover."""

    @classmethod
    def setUpClass(cls):
        cls.caddy = find_caddy()
        if not cls.caddy:
            raise unittest.SkipTest('caddy binary not found; set CADDY or add caddy to PATH')
        source = CADDYFILE.read_text(encoding='utf-8')
        public_site_placeholder(source)
        cls._tmpdir = tempfile.TemporaryDirectory(prefix='movie-m3-cookie-')
        workdir = Path(cls._tmpdir.name)
        cert, key = make_self_signed(workdir)
        cls.movie_port = free_port()
        cls.emby_port = free_port()
        cls._records = []
        cls._lock = threading.Lock()
        _CookieEchoStub.records = cls._records
        _CookieEchoStub.lock = cls._lock
        cls._stub = ThreadingHTTPServer(('127.0.0.1', 0), _CookieEchoStub)
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

    def setUp(self):
        with self._lock:
            self._records.clear()

    def test_cinema_cookie_stripped_from_request_and_response(self):
        status, hdrs, body = request_https(
            self.emby_port, '/discover/',
            headers={'Cookie': 'cinema=forged-admin; theme=dark; other=1'},
        )
        self.assertEqual(status, 200, body[:200])
        with self._lock:
            hits = list(self._records)
        self.assertEqual(len(hits), 1, hits)
        seen = hits[0]['cookie']
        self.assertNotIn('cinema=', seen.lower() if seen else '')
        self.assertIn('theme=dark', seen)
        self.assertIn('other=1', seen)
        # Body is the stub echo of the (stripped) Cookie.
        self.assertTrue(body.startswith(b'ECHO-COOKIE:'), body[:200])
        self.assertNotIn(b'cinema=', body.lower())
        set_cookies = header_values(hdrs, 'Set-Cookie')
        for value in set_cookies:
            self.assertFalse(value.lower().lstrip().startswith('cinema='),
                             'response Set-Cookie leaked cinema: %r' % set_cookies)
        # Unrelated cookies from portal may pass through (not required to drop).
        self.assertEqual(cache_control_values(hdrs), [NO_STORE])

    def test_movie2_discover_still_forwards_cinema_cookie(self):
        """movie2 is unchanged: cinema Cookie is forwarded and Set-Cookie is kept."""
        status, hdrs, body = request_https(
            self.movie_port, '/discover/',
            headers={'Cookie': 'cinema=real-admin; theme=dark'},
        )
        self.assertEqual(status, 200, body[:200])
        with self._lock:
            hits = list(self._records)
        self.assertEqual(len(hits), 1, hits)
        self.assertIn('cinema=real-admin', hits[0]['cookie'])
        set_cookies = header_values(hdrs, 'Set-Cookie')
        self.assertTrue(
            any(v.lower().lstrip().startswith('cinema=') for v in set_cookies),
            'movie2 should still emit cinema Set-Cookie, got %r' % set_cookies)


class Movie3RedirectDualEntryTests(unittest.TestCase):
    """D4/D6 307 Location rewrite on both /movie and /emby entries."""

    @classmethod
    def setUpClass(cls):
        cls.caddy = find_caddy()
        if not cls.caddy:
            raise unittest.SkipTest('caddy binary not found; set CADDY or add caddy to PATH')
        source = CADDYFILE.read_text(encoding='utf-8')
        public_site_placeholder(source)
        cls._tmpdir = tempfile.TemporaryDirectory(prefix='movie-m3-redir-')
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

    def _assert_clean_307(self, path, method):
        status, hdrs, body = request_https(self.emby_port, path, method=method)
        self.assertEqual(status, 307, '%s %s status=%s body=%r' % (method, path, status, body[:200]))
        locs = header_values(hdrs, 'Location')
        self.assertEqual(len(locs), 1, locs)
        loc = locs[0]
        self.assertEqual(loc, '/openlist/d/aliyun/Movies/X.mkv?sign=good', loc)
        self.assertNotIn('movie2', loc.lower())
        self.assertFalse(loc.lower().startswith('http'))
        if method == 'HEAD':
            self.assertEqual(body, b'')
        self.assertNotIn(b'movie2', body.lower())
        self.assertNotRegex(body, rb'(?i)https?://')
        self.assertEqual(header_values(hdrs, 'Content-Length'), [str(len(body))])
        self.assertEqual(cache_control_values(hdrs), [NO_STORE])
        for key, value in (hdrs.items() if hasattr(hdrs, 'items') else hdrs):
            self.assertNotIn('movie2', value.lower(), '%s: %s' % (key, value))
            self.assertNotRegex(value, r'(?i)https?://')

    def test_d6_307_on_movie_prefix_and_emby_prefix(self):
        for path in (
                '/movie/Videos/' + VIDEO_ID + '/stream?static=true',
                '/emby/Videos/' + VIDEO_ID + '/stream?static=true',
        ):
            for method in ('GET', 'HEAD'):
                with self.subTest(path=path, method=method):
                    self._assert_clean_307(path, method)


if __name__ == '__main__':
    unittest.main()
