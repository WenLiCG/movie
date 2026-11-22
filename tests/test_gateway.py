"""Static checks of the public gateway; `caddy validate` and a live run are recorded in HANDOFF.md."""
from pathlib import Path
import re
import unittest

CADDYFILE = (Path(__file__).resolve().parents[1] / 'gateway/Caddyfile').read_text(encoding='utf-8')


class GatewayTests(unittest.TestCase):
    def test_admin_cookie_is_stripped_before_emby_without_touching_other_cookies(self):
        relay = CADDYFILE[CADDYFILE.rindex('    handle {'):]
        self.assertIn('reverse_proxy relay:8095', relay)
        found = re.search(r'header_up Cookie "([^"]+)" "([^"]*)"', relay)
        self.assertIsNotNone(found, 'relay route must strip the cinema admin cookie')
        pattern, replacement = found[1], found[2].replace('$1', r'\1')
        strip = lambda value: re.sub(pattern, replacement, value)  # RE2 and Python agree on this subset
        self.assertEqual(strip('cinema=SECRET'), '')
        self.assertEqual(strip('cinema=SECRET; emby=1'), 'emby=1')
        self.assertNotIn('SECRET', strip('a=1; cinema=SECRET; b=2'))
        self.assertEqual(strip('notcinema=keep; x=1'), 'notcinema=keep; x=1')
        self.assertEqual(strip('x=notcinema=1'), 'x=notcinema=1')

    def test_admin_routes_stay_gated_and_site_sends_hsts(self):
        for route in ('/search/*', '/sonarr/*', '/openlist/*', '/qb/*'):
            block = CADDYFILE[CADDYFILE.index(route):]
            self.assertLess(block.index('import cinema_admin'), block.index('reverse_proxy'), route)
        self.assertIn('Strict-Transport-Security "max-age=31536000"', CADDYFILE)


if __name__ == '__main__':
    unittest.main()
