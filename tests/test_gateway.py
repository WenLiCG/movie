"""Static checks of the public gateway; `caddy validate` and a live run are recorded in HANDOFF.md."""
from pathlib import Path
import re
import unittest

CADDYFILE = (Path(__file__).resolve().parents[1] / 'gateway/Caddyfile').read_text(encoding='utf-8')


class GatewayTests(unittest.TestCase):
    def test_upstreams_strip_the_portal_cookie_and_keep_native_sessions(self):
        for service in ('relay:8095', 'prowlarr:9696', 'sonarr:8989', 'openlist:5244', 'qbittorrent:8080'):
            self.assertRegex(CADDYFILE, rf'reverse_proxy {service} \{{\s*import without_portal_cookie\b')
        found = re.search(r'\(without_portal_cookie\) \{\s*header_up Cookie "([^"]+)" "([^"]*)"', CADDYFILE)
        self.assertIsNotNone(found)
        pattern, replacement = found[1], found[2].replace('$1', r'\1')
        strip = lambda value: re.sub(pattern, replacement, value)  # RE2 and Python agree on this subset
        self.assertEqual(strip('cinema=SECRET'), '')
        self.assertEqual(strip('cinema=SECRET; emby=1'), 'emby=1')
        self.assertNotIn('SECRET', strip('a=1; cinema=SECRET; b=2'))
        self.assertEqual(strip('cinema=OLD; cinema=NEW; native=1'), 'native=1')
        self.assertEqual(strip('notcinema=keep; x=1'), 'notcinema=keep; x=1')
        self.assertEqual(strip('x=notcinema=1'), 'x=notcinema=1')

    def test_admin_routes_stay_gated_and_site_sends_hsts(self):
        auth = CADDYFILE.split('forward_auth portal:8097 {', 1)[1].split('\n    }', 1)[0]
        self.assertIn('header_up Cookie "cinema={http.request.cookie.cinema}"', auth)
        self.assertIn('header_up -Authorization', auth)
        for route in ('/search/*', '/sonarr/*', '/openlist/*', '/qb/*'):
            block = CADDYFILE.split(route, 1)[1].split('\n    }', 1)[0]
            self.assertLess(block.index('import cinema_admin'), block.index('reverse_proxy'), route)
        self.assertIn('Strict-Transport-Security "max-age=31536000"', CADDYFILE)


if __name__ == '__main__':
    unittest.main()
