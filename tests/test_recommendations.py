"""Run: python -m unittest discover -s tests -p test_recommendations.py"""
import json
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit
from portal.server import App


class RecommendationsTests(unittest.TestCase):
    def setUp(self):
        self.app, self.client = App(), Mock()
        self.sites = [{'id': 1, 'name': 'HDSky', 'enable': True, 'supportsRss': True},
                      {'id': 2, 'name': 'Disabled', 'enable': False, 'supportsRss': True},
                      {'id': 3, 'name': 'Search only', 'enable': True, 'supportsRss': False}]
        self.values = []
        self.client.json.side_effect = lambda method, path: self.sites if path == '/api/v1/indexer' else self.values
        self.app.client = lambda service: self.client
        patched = patch('portal.server.metadata', return_value={})
        self.metadata = patched.start()
        self.addCleanup(patched.stop)

    def release(self, title, seeders, imdb=0, category=2000, site=1, rid='1'):
        return {'title': title, 'seeders': seeders, 'imdbId': imdb, 'indexerId': site,
                'categories': [{'id': category}], 'downloadUrl': 'private-passkey', 'guid': 'private-guid',
                'infoUrl': 'https://indexer.invalid/details.php?id=' + rid}

    def test_chinese_display_keeps_search_and_release_and_matches_site_and_resource(self):
        self.sites.append({'id': 4, 'name': 'Other site', 'enable': True, 'supportsRss': True})
        self.values = [self.release('Spider-Man.Brand.New.Day.2026.2160p', 90, rid='42'),
                       self.release('The.Lead.S01.2026.2160p', 80, category=5000, rid='43'),
                       self.release('Other.Film.2026.1080p', 70, site=4, rid='42')]
        self.metadata.side_effect = lambda site, query, kind: {
            1: {'42': {'subtitle': '蜘蛛侠：崭新之日 / Spider-Man: Brand New Day | 主演：演员'},
                '43': {'subtitle': '主角 全48集 | 主演：演员 【去头尾广告】'}},
            4: {'42': {'subtitle': '其他电影【国语中字】'}, '43': {'subtitle': '不能串站'}}}[site['id']]
        items = self.app.recommendations()['items']
        self.assertEqual([v['title'] for v in items], ['蜘蛛侠：崭新之日', '主角', '其他电影'])
        self.assertEqual([v['query'] for v in items], ['Spider-Man Brand New Day', 'The Lead', 'Other Film'])
        self.assertEqual(items[0]['release'], self.values[0]['title'])
        self.assertNotIn('private-', json.dumps(items))
        self.assertCountEqual([(c.args[0]['id'], *c.args[1:]) for c in self.metadata.call_args_list], [(1, '', 'all'), (4, '', 'all')])

    def test_missing_non_chinese_or_release_note_only_metadata_keeps_original(self):
        self.values = [self.release(f'Film{i}.2026.1080p', 10-i, rid=str(i)) for i in range(6)]
        self.metadata.return_value = {'0': {'subtitle': 'English Name / 中文别名'},
                                      '1': {'subtitle': '【简繁中字】'}, '2': {'subtitle': '中文' * 41},
                                      '3': {'subtitle': ''}, '999': {'subtitle': '资源 ID 不匹配'}}
        self.assertEqual([v['title'] for v in self.app.recommendations()['items']], [f'Film{i}' for i in range(6)])

    def test_metadata_failure_preserves_ranked_recommendations(self):
        self.values = [self.release('Film.2026.1080p', 8)]
        self.metadata.side_effect = OSError('private-cookie-error')
        result = self.app.recommendations()
        self.assertEqual(result['items'][0]['title'], 'Film')
        self.assertEqual(result['message'], '')
        self.assertNotIn('private-', json.dumps(result))

    def test_enabled_sites_seed_order_identity_dedup_and_private_fields(self):
        self.values = [self.release('Film.2026.1080p', 8, 42), self.release('Film.2026.2160p', 20, 42),
                       self.release('Show.S01E03.1080p', 12, 60, 5000), self.release('Disabled', 999, site=2),
                       self.release('2001.A.Space.Odyssey.1968.1080p', 10), self.release('No Seeds', 0),
                       self.release('Music', 100, category=3000)]
        result = self.app.recommendations()
        self.assertEqual([v['query'] for v in result['items']], ['Film', 'Show', '2001 A Space Odyssey'])
        self.assertEqual([v['kind'] for v in result['items']], ['movie', 'tv', 'movie'])
        self.assertNotIn('private-', json.dumps(result))
        query = parse_qs(urlsplit(self.client.json.call_args.args[1]).query, keep_blank_values=True)
        self.assertEqual(query['indexerIds'], ['1'])
        self.assertEqual(query['categories'], ['2000', '5000'])
        self.assertEqual(query['query'], [''])

    def test_cache_expiry_and_disabled_site_invalidation(self):
        self.values = [self.release('Film.2026.1080p', 8)]
        with patch('portal.server.time.time', return_value=100):
            self.app.recommendations(); self.app.recommendations()
        self.assertEqual(sum('/search?' in call.args[1] for call in self.client.json.call_args_list), 1)
        self.assertEqual(self.metadata.call_count, 1)
        with patch('portal.server.time.time', return_value=1901): self.app.recommendations()
        self.assertEqual(sum('/search?' in call.args[1] for call in self.client.json.call_args_list), 2)
        self.assertEqual(self.metadata.call_count, 2)
        self.sites[0]['enable'] = False
        self.assertEqual(self.app.recommendations()['items'], [])

    def test_limit_and_failure_are_bounded(self):
        self.values = [self.release(f'Film{i}.2026.1080p', i+1) for i in range(20)]
        self.assertEqual(len(self.app.recommendations()['items']), 6)
        self.app.recommendations_cache = None
        def failing(method, path):
            if path == '/api/v1/indexer': return self.sites
            raise ValueError('private-provider-message')
        self.client.json.side_effect = failing
        with patch('portal.server.time.time', return_value=100):
            result = self.app.recommendations()
        self.assertEqual(result['items'], [])
        self.assertNotIn('private-', json.dumps(result))
        self.assertEqual(self.app.recommendations_cache['expires'], 160)
