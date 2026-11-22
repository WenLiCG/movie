import hashlib
import http.cookiejar
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.request import Request, build_opener, HTTPCookieProcessor
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from portal.server import App, Handler, Metrics, tags, archive_error
from integration import APIError
from portal.metadata import Titles, metadata


class PortalTests(unittest.TestCase):
    def test_chinese_names_require_matching_provider_ids(self):
        app = App()
        client = Mock()
        client.json.return_value = [{'Name':'错误的中文剧名','ProviderIds':{'Tvdb':'123'}}, {'Name':'牧神记','ProviderIds':{'Tvdb':'454898'}}]
        app.client = lambda name: client
        values = [{'tvdbId':454898,'title':'Tales of Herding Gods'}]
        localized = app.localize({'token':'private-token'}, values, '牧神记')
        self.assertEqual(localized[454898]['title'], '牧神记')
        self.assertEqual(localized[454898]['original_title'], 'Tales of Herding Gods')
        self.assertEqual(values[0]['title'], 'Tales of Herding Gods')
        self.assertNotIn('private-token', json.dumps(localized))

    def test_archive_errors_distinguish_network_from_authorization(self):
        self.assertIn('传输连接中断', archive_error('network_or_protocol'))
        self.assertIn('授权已失效', archive_error('openlist_code_401'))
        self.assertIn('后台上传任务失败', archive_error('native_upload_failed'))
        self.assertEqual(archive_error(None), '')
    def test_nexus_subtitles_and_all_badges_are_plain_text(self):
        parser = Titles()
        parser.feed('''<table class="torrentname"><tr><td><div class="torrent-title"><a href="details.php?id=42">Main</a></div><div class="torrent-smalldescr"><span class="torrent-tag torrent-team">CMCTV</span><span class="torrent-tag torrent-internal"><span>官方</span></span><span class="torrent-tag">合集</span><span class="torrent-tag">中字</span><span>空枪 | 类型: 剧情/犯罪 &amp; 其他</span></div></td></tr></table>
        <table class="torrentname"><tr><td><a href="details.php?id=99"><span>Main title</span></a><br><span class="optiontag">中字</span><span>中文副标题</span></td></tr></table>''')
        self.assertEqual(parser.items['42']['labels'], ['CMCTV', '官方', '合集', '中字'])
        self.assertEqual(parser.items['42']['subtitle'], '空枪 | 类型: 剧情/犯罪 & 其他')
        self.assertEqual(parser.items['99'], {'subtitle': '中文副标题', 'labels': ['中字']})

    def test_metadata_keeps_cookie_at_configured_origin_and_rejects_unsafe_url(self):
        indexer = {'indexerUrls': ['https://example.invalid/'], 'fields': [{'name':'definitionFile','value':'springsunday'}, {'name':'cookie','value':'private-cookie'}]}
        with patch('portal.metadata.Client') as client:
            client.return_value.request.return_value = b'<table class="torrentname"><a href="details.php?id=3">Film</a></table>'
            self.assertIn('3', metadata(indexer, 'Film'))
            self.assertEqual(client.return_value.opener.addheaders[0], ('Cookie', 'private-cookie'))
            self.assertNotIn('private-cookie', client.return_value.request.call_args.args[1])
        indexer['indexerUrls'] = ['https://user:secret@example.invalid/']
        with self.assertRaises(ValueError): metadata(indexer, 'Film')

    def test_status_distinguishes_missing_configuration_from_saved_but_broken(self):
        with tempfile.TemporaryDirectory() as temp:
            app = App(Path(temp))
            client = Mock()
            def native(method, path, body=None):
                if path == '/api/v1/indexer': return []
                if path in {'/api/v1/health','/api/v3/rootfolder','/api/v3/downloadclient','/api/v1/applications','/api/v3/indexer'}: return []
                return {}
            client.json.side_effect = native
            app.client = lambda name: client
            app.qb = Mock(side_effect=ValueError('not configured'))
            result = app.status()
            self.assertEqual({k for k,v in result['checks'].items() if not v['complete']}, {'cloud','downloader','indexers','television','uploader','metadata'})
            self.assertTrue(result['checks']['retention']['complete'])
            (Path(temp)/'config.json').write_text(json.dumps({'openlist_token':'private'}))
            app.ol = Mock(side_effect=ValueError('expired'))
            self.assertFalse(app.status()['configured'])

    def test_episode_download_targets_only_selected_ids_and_never_changes_monitoring(self):
        with tempfile.TemporaryDirectory() as temp:
            app = App(Path(temp))
            native = Mock()
            native.json.side_effect = lambda method,path,body=None: ({'tvdbId':42} if path=='/api/v3/series/9' else {'records':[]} if '/queue?' in path else [{'id':i,'seriesId':9,'seasonNumber':1,'episodeNumber':i,'hasFile':i==3} for i in (1,2,3)] if method=='GET' else {})
            app.client = lambda name: native
            app.require_version = Mock(return_value={})
            app.download_episodes({'series_id':9,'episode_ids':[2]})
            native.json.assert_called_with('POST','/api/v3/command',{'name':'EpisodeSearch','episodeIds':[2]})
            self.assertFalse(any(call.args[0] == 'PUT' for call in native.json.call_args_list))
            for ids in ([1,1], [3], [999], [], ['2']):
                with self.assertRaises(ValueError): app.download_episodes({'series_id':9,'episode_ids':ids})

    def test_prepare_existing_series_keeps_monitoring_and_quality(self):
        with tempfile.TemporaryDirectory() as temp:
            app = App(Path(temp))
            native = Mock()
            native.json.side_effect = lambda method, path, body=None: ([{'id':9,'tvdbId':42,'monitored':True,'qualityProfileId':77}] if path == '/api/v3/series' else ([] if path == '/api/v3/tag' else ({'records':[]} if '/queue?' in path else {'tvdbId':42} if path=='/api/v3/series/9' else [{'id':2,'seasonNumber':1,'episodeNumber':2}])))
            app.client = lambda name: native
            app.require_version = Mock(return_value={})
            result = app.episodes({'series':{'42':{'tvdbId':42}}}, {'id':'42'})
            self.assertEqual(result['series_id'],9)
            self.assertTrue(all(call.args[0] == 'GET' for call in native.json.call_args_list))
    def test_title_tags_and_host_cpu_deltas(self):
        self.assertEqual(tags('Title.2160p.HEVC'), ('4K', 'H.265'))
        self.assertEqual(tags('Title 1080p H264'), ('1080p', 'H.264'))
        self.assertEqual(tags('Unknown'), ('未知', '未知'))
        with tempfile.TemporaryDirectory() as temp:
            proc = Path(temp)
            (proc / 'meminfo').write_text('MemTotal: 1000 kB\nMemAvailable: 400 kB\n')
            (proc / 'stat').write_text('cpu  10 0 10 80 0 0 0 0 90 0\n')
            metrics = Metrics()
            self.assertIsNone(metrics.read(proc, proc)['cpu'])
            (proc / 'stat').write_text('cpu  20 0 20 100 0 0 0 0 180 0\n')
            result = metrics.read(proc, proc)
            self.assertEqual(result['cpu'], 50)
            self.assertEqual(result['memory']['used'], 600 * 1024)

    def test_auth_csrf_settings_and_revocation_over_http(self):
        with tempfile.TemporaryDirectory() as temp:
            app = App(Path(temp))
            emby = Mock()
            emby.json.side_effect = lambda method, path, *args: ({'AccessToken': 'private-emby-token', 'User': {'Id': 'admin', 'Policy': {'IsAdministrator': True}}} if 'Authenticate' in path else {'Policy': {'IsAdministrator': True}})
            app.client = lambda name: emby
            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            server.app = app
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            base = 'http://127.0.0.1:' + str(server.server_port)
            opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
            def call(path, body=None, headers=None):
                req = Request(base + '/discover/api/' + path, None if body is None else json.dumps(body).encode(), {'Content-Type': 'application/json', 'Origin': base, **(headers or {})})
                try:
                    with opener.open(req) as r:
                        return r, json.loads(r.read())
                except HTTPError as error:
                    error.close()
                    raise
            try:
                with self.assertRaises(HTTPError) as err:
                    call('metrics')
                self.assertEqual(err.exception.code, 401)
                with self.assertRaises(HTTPError) as err:
                    call('playbacks')
                self.assertEqual(err.exception.code, 401)
                with self.assertRaises(HTTPError) as err:
                    call('recommendations')
                self.assertEqual(err.exception.code, 401)
                with self.assertRaises(HTTPError) as err:
                    call('login', {'username': 'admin', 'password': 'test'}, {'Origin': 'https://evil.example'})
                self.assertEqual(err.exception.code, 403)
                with patch('portal.server.emby_defaults') as initialize:
                    response, login = call('login', {'username': 'admin', 'password': 'test'})
                    initialize.assert_called_once_with(emby)
                self.assertIn('HttpOnly', response.headers['Set-Cookie'])
                self.assertNotIn('private-emby-token', json.dumps(login))
                with self.assertRaises(HTTPError) as err:
                    call('settings', {'retention_days': 3})
                self.assertEqual(err.exception.code, 403)
                call('settings', {'retention_days': 3}, {'X-CSRF-Token': login['csrf']})
                self.assertEqual(app.settings()['retention_days'], 3)
                with patch('portal.server.emby_defaults') as defaults:
                    with self.assertRaises(HTTPError) as denied:
                        call('metadata-defaults', {})
                    self.assertEqual(denied.exception.code, 403)
                    _, configured = call('metadata-defaults', {}, {'X-CSRF-Token': login['csrf']})
                    self.assertIn('补充缺失', configured['message'])
                    defaults.assert_called_once_with(emby)
                with self.assertRaises(HTTPError):
                    call('settings', {'retention_days': -1}, {'X-CSRF-Token': login['csrf']})
                self.assertEqual(app.settings()['retention_days'], 3)
                emby.json.side_effect = APIError(502, 'service unavailable')
                with self.assertRaises(HTTPError) as unavailable:
                    call('settings', {'retention_days': 0}, {'X-CSRF-Token': login['csrf']})
                self.assertEqual(unavailable.exception.code, 503)
                self.assertEqual(app.settings()['retention_days'], 3)
                emby.json.side_effect = lambda *args: {'Policy': {'IsAdministrator': True}}
                self.assertEqual(call('session')[1]['csrf'], login['csrf'])
                emby.json.side_effect = lambda *args: {'Policy': {'IsAdministrator': False}}
                with self.assertRaises(HTTPError) as err:
                    call('session')
                self.assertEqual(err.exception.code, 401)
            finally:
                server.shutdown(); server.server_close(); worker.join()

    def test_search_hides_download_urls_and_preserves_tv_identity(self):
        app = App()
        client = Mock()
        client.json.return_value = [{'title': 'Film.1080p.x264', 'size': 100, 'indexer': 'Test', 'indexerId': 2, 'guid': 'secret-guid', 'downloadUrl': 'https://private/passkey', 'indexerFlags': ['freeleech']}]
        app.client = lambda name: client
        session = {}
        result = app.search(session, 'Film', 'movie')
        self.assertNotIn('passkey', json.dumps(result))
        self.assertEqual(result['items'][0]['codec'], 'H.264')
        self.assertTrue(result['items'][0]['free'])
        self.assertEqual(next(iter(session['results'].values()))[1]['guid'], 'secret-guid')

    def test_movie_search_sorts_by_size_and_joins_metadata_by_site_and_id(self):
        app = App()
        client = Mock()
        releases = [{'title':'Film 1080p','size':size,'indexer':'Site','indexerId':1,'guid':str(i),'infoUrl':f'https://example.invalid/details.php?id={i}'} for i,size in [(2,200),(1,100)]]
        client.json.side_effect = lambda method,path: ([{'id':1,'enable':True,'fields':[]}] if path == '/api/v1/indexer' else releases)
        app.client = lambda name: client
        with patch('portal.server.metadata', return_value={'1':{'subtitle':'中文名','labels':['官方','中字']}}):
            result = app.search({},'Film','movie')
        self.assertEqual([i['size'] for i in result['items']], [100,200])
        self.assertEqual(result['items'][0]['subtitle'],'中文名')
        self.assertEqual(result['items'][0]['labels'],['官方','中字'])
        self.assertFalse(result['items'][1]['metadata_available'])
        self.assertEqual(result['warnings'], [])
        with patch('portal.server.metadata', side_effect=ValueError('private provider response')):
            unavailable = app.search({}, 'Film', 'movie')
        self.assertEqual(len(unavailable['items']), 2)
        self.assertTrue(unavailable['warnings'])
        self.assertNotIn('private provider response', json.dumps(unavailable))

    def test_unmatched_text_preview_does_not_suppress_tvdb_lookup(self):
        app = App()
        emby = Mock()
        app.client = lambda _: emby
        series = [{'tvdbId':42,'title':'Example'}]
        emby.json.side_effect = [[], [{'Name':'示例剧集','ProviderIds':{'Tvdb':'42'}}]]
        self.assertEqual(app.localize({'token':'admin'}, series, 'Example S01')[42]['title'], 'Example')
        self.assertEqual(app.localize({'token':'admin'}, series)[42]['title'], '示例剧集')

    def test_cloud_wizard_sets_driver_defaults_and_keeps_secrets_server_side(self):
        with tempfile.TemporaryDirectory() as temp:
            app = App(Path(temp))
            native = Mock()
            calls = []
            def call(method, path, body=None):
                calls.append((method, path, body))
                if '/storage/list?' in path: data = {'content': []}
                elif path == '/api/fs/get': data = {'is_dir': True, 'provider': 'AliyundriveOpen'}
                elif path == '/api/fs/list': data = {'content': []}
                else: data = {}
                return {'code': 200, 'data': data}
            native.json.side_effect = call
            app.openlist = lambda session, body: (native, 'private-service-token')
            with patch('portal.server.read_api_key', return_value='a' * 32), patch('portal.server.connect_services') as connect:
                result = app.configure({'username': 'admin', 'password': 'private-qb-password'}, {'refresh_token': 'private-refresh-token', 'qb_password':'private-qb-password'})
            storage = next(c[2] for c in calls if c[1] == '/api/admin/storage/create')
            addition = json.loads(storage['addition'])
            self.assertEqual(storage['mount_path'], '/aliyun')
            self.assertEqual(addition['root_folder_id'], 'root')
            self.assertTrue(addition['use_online_api'])
            self.assertFalse(addition['internal_upload'])
            self.assertTrue(addition['rapid_upload'])
            self.assertNotIn('private-', json.dumps(result))
            self.assertEqual(app.config()['qb_password'], 'private-qb-password')
            self.assertEqual((Path(temp) / 'relay-token/openlist-token').read_text(), 'private-service-token')
            connect.assert_called_once()

    def test_new_series_is_unmonitored_until_a_version_is_saved(self):
        with tempfile.TemporaryDirectory() as temp:
            app = App(Path(temp))
            native = Mock()
            def call(method,path,body=None):
                if path == '/api/v3/series': return [] if method == 'GET' else {'id':10}
                if path == '/api/v3/qualityprofile/schema': return {'items':[{'quality':{'id':1,'name':'WEBDL-1080p'}}]}
                if path == '/api/v3/qualityprofile': return [] if method == 'GET' else {'id':5}
                return {}
            native.json.side_effect=call
            app.client=lambda name:native
            session={'series':{'42':{'tvdbId':42,'title':'Example','seasons':[{'seasonNumber':1}]}}}
            with self.assertRaises(ValueError):app.follow(session,{'id':'42','mode':'all'})
            app.follow(session,{'id':'42','mode':'manual'})
            added=next(c.args[2] for c in native.json.call_args_list if c.args[:2]==('POST','/api/v3/series'))
            self.assertFalse(added['monitored'])
            self.assertFalse(added['addOptions']['searchForMissingEpisodes'])
            self.assertEqual(added['addOptions']['monitor'],'none')
            self.assertFalse(any(c.args[:2]==('POST','/api/v3/command') for c in native.json.call_args_list))


if __name__ == '__main__':
    unittest.main()
