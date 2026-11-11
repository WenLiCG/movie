from pathlib import Path
import re
import unittest


CADDYFILE = Path(__file__).resolve().parents[1] / 'gateway' / 'Caddyfile'
NO_STORE_HEADER = 'header >Cache-Control "private, no-store"'


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

    def test_cache_control_header_occurs_once_in_entire_file(self):
        headers = re.findall(
            r'(?m)^\s*header\s+>?Cache-Control\b[^\n]*', self.config)
        self.assertEqual([header.strip() for header in headers], [NO_STORE_HEADER])

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


if __name__ == '__main__':
    unittest.main()
