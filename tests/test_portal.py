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

    def test_downloader_status_detects_a_misdirected_prowlarr_client(self):
        with tempfile.TemporaryDirectory() as temp:
            app = App(Path(temp))
            qb, native = Mock(), Mock()
            app.qb = lambda:qb
            app.client = lambda _:native
            qb.json.return_value = {category:{'savePath':'/media/downloads/'+folder} for category, folder in (
                ('cinema-movies','movies'), ('cinema-tv','tv-manual'), ('sonarr-tv','tv'))}
            clients = [{'name':name, 'enable':True, 'implementation':'QBittorrent', 'fields':[
                {'name':'host','value':'qbittorrent'}, {'name':'port','value':8080}, {'name':'category','value':category}]}
                for name,category in [('Cinema qB','cinema-movies'), ('Cinema qB TV','cinema-tv')]]
            native.json.side_effect = lambda method,path: clients if path == '/api/v1/downloadclient' else []
            self.assertTrue(app.status()['checks']['downloader']['complete'])
            clients[0]['fields'][0]['value'] = 'old-host'
            self.assertFalse(app.status()['checks']['downloader']['complete'])

    def test_episode_download_targets_only_selected_ids_and_never_changes_monitoring(self):
        with tempfile.TemporaryDirectory() as temp:
            app = App(Path(temp))
            native = Mock()
            native.json.side_effect = lambda method,path,body=None: ({'tvdbId':42} if path=='/api/v3/series/9' else {'records':[], 'totalRecords':0} if '/queue?' in path else [{'id':i,'seriesId':9,'seasonNumber':1,'episodeNumber':i,'hasFile':i==3} for i in (1,2,3)] if method=='GET' else {})
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
            native.json.side_effect = lambda method, path, body=None: ([{'id':9,'tvdbId':42,'monitored':True,'qualityProfileId':77}] if path == '/api/v3/series' else ([] if path == '/api/v3/tag' else ({'records':[], 'totalRecords':0} if '/queue?' in path else {'tvdbId':42} if path=='/api/v3/series/9' else [{'id':2,'seasonNumber':1,'episodeNumber':2}])))
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

    def _serve(self, app):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.app = app
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), worker.join()))
        base = 'http://127.0.0.1:' + str(server.server_port)
        opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        def call(path, body=None, headers=None):
            req = Request(base + '/discover/api/' + path, None if body is None else json.dumps(body).encode(),
                          {'Content-Type': 'application/json', 'Origin': base, **(headers or {})})
            try:
                with opener.open(req) as r:
                    return r, json.loads(r.read())
            except HTTPError as error:
                payload = json.loads(error.read() or b'{}')
                error.close()
                return error, payload
        return server, call

    def test_logout_revokes_local_session_even_when_emby_is_unreachable(self):
        with tempfile.TemporaryDirectory() as temp:
            app = App(Path(temp))
            emby = Mock()
            emby.json.side_effect = lambda method, path, *args: ({'AccessToken': 'private-emby-token', 'User': {'Id': 'admin', 'Policy': {'IsAdministrator': True}}} if 'Authenticate' in path else {'Policy': {'IsAdministrator': True}})
            app.client = lambda name: emby
            _, call = self._serve(app)
            with patch('portal.server.emby_defaults'):
                _, login = call('login', {'username': 'admin', 'password': 'test'})
            self.assertEqual(len(app.sessions), 1)
            # Logout is still a state change and needs the session's CSRF token.
            response, _ = call('logout', {})
            self.assertEqual(response.status, 403)
            self.assertEqual(len(app.sessions), 1)
            # Emby outage: both the admin re-check and the remote logout fail.
            emby.json.side_effect = APIError(502, 'down')
            emby.request.side_effect = ValueError('down')
            response, _ = call('logout', {}, {'X-CSRF-Token': login['csrf']})
            self.assertEqual(response.status, 200)
            self.assertIn('Max-Age=0', response.headers['Set-Cookie'])
            self.assertEqual(app.sessions, {})
            # Repeating logout with a stale page stays harmless.
            response, _ = call('logout', {}, {'X-CSRF-Token': login['csrf']})
            self.assertEqual(response.status, 200)

    def test_wrong_emby_password_gets_a_specific_message(self):
        with tempfile.TemporaryDirectory() as temp:
            app = App(Path(temp))
            emby = Mock()
            emby.json.side_effect = APIError(401, '/Users/AuthenticateByName 返回 HTTP 401')
            app.client = lambda name: emby
            _, call = self._serve(app)
            response, payload = call('login', {'username': 'admin', 'password': 'wrong'})
            self.assertEqual(response.status, 400)
            self.assertIn('用户名或密码不正确', payload['error'])
            emby.json.side_effect = APIError(500, '/Users/AuthenticateByName 返回 HTTP 500')
            response, payload = call('login', {'username': 'admin', 'password': 'wrong'})
            self.assertNotIn('用户名或密码不正确', payload['error'])

    def test_stalled_client_does_not_pin_a_worker_thread(self):
        import socket
        self.assertTrue(Handler.timeout and 0 < Handler.timeout <= 60)
        with patch.object(Handler, 'timeout', 0.5):
            server, _ = self._serve(App())
            with socket.create_connection(('127.0.0.1', server.server_port), timeout=5) as sock:
                sock.sendall(b'POST /discover/api/login HTTP/1.1\r\nHost: x\r\n')  # never finishes the headers
                self.assertEqual(sock.recv(1024), b'')  # server closed the idle connection

    def test_authored_user_errors_surface_while_internal_and_upstream_errors_stay_masked(self):
        with tempfile.TemporaryDirectory() as temp:
            app = App(Path(temp))
            emby = Mock()
            emby.json.side_effect = lambda method, path, *args: (
                {'AccessToken': 'private-emby-token', 'User': {'Id': 'admin', 'Policy': {'IsAdministrator': True}}}
                if 'Authenticate' in path else {'Policy': {'IsAdministrator': True}})
            app.client = lambda name: emby
            _, call = self._serve(app)
            with patch('portal.server.emby_defaults'):
                _, login = call('login', {'username': 'admin', 'password': 'test'})
            headers = {'X-CSRF-Token': login['csrf']}
            for target, message in [
                ('metadata_defaults', '同名媒体库使用了其他目录，请先在 Emby 检查。'),
                ('playbacks', '播放记录暂时无法读取，请检查播放服务及目录权限。'),
                ('download_episodes', '所选集数无效、本地或云端已有，或正在下载，请刷新后重试。'),
                ('configure', 'OpenList 管理员登录失败，请检查 OpenList 用户名和密码。'),
                ('configure', 'qB 登录失败；请输入当前网页登录密码。'),
            ]:
                with patch.object(app, target, side_effect=ValueError(message)):
                    err, payload = (call('playbacks') if target == 'playbacks'
                                    else call(target.replace('_', '-'), {}, headers))
                    self.assertEqual(err.code, 400, target)
                    self.assertEqual(payload['error'], message)
            for secret_err in (APIError(500, 'https://secret.example/token=abc'),
                               ValueError('internal secret detail'),
                               KeyError('secret_field')):
                with patch.object(app, 'metadata_defaults', side_effect=secret_err):
                    err, payload = call('metadata-defaults', {}, headers)
                    self.assertEqual(err.code, 400)
                    self.assertEqual(payload['error'], '操作未完成，请检查输入、服务登录或配置后重试。')

    def test_openlist_admin_login_failure_gives_credential_specific_message(self):
        with tempfile.TemporaryDirectory() as temp:
            app = App(Path(temp))
            ol = Mock()
            app.client = lambda name: ol
            ol.json.return_value = {'code': 400, 'message': 'password is incorrect'}
            with self.assertRaisesRegex(ValueError, 'OpenList 管理员登录失败'):
                app.openlist({'username': 'admin'}, {'openlist_password': 'wrong'})
            ol.json.side_effect = APIError(401, '/api/auth/login 返回 HTTP 401')
            with self.assertRaisesRegex(ValueError, 'OpenList 管理员登录失败'):
                app.openlist({'username': 'admin'}, {'openlist_password': 'wrong'})

    def test_localize_caches_confirmed_absence_24h_and_retries_transient_failure_in_5m(self):
        app = App()
        emby = Mock()
        app.client = lambda _: emby
        series = [{'tvdbId': 101, 'title': 'Western Show'}, {'tvdbId': 102, 'title': 'Flaky Show'}]
        emby.json.side_effect = lambda method, path, body: (
            (_ for _ in ()).throw(OSError('timeout')) if body['SearchInfo']['ProviderIds']['Tvdb'] == '102' else [])
        with patch('portal.server.time.time', return_value=1000):
            first = app.localize({'token': 't'}, series)
        self.assertEqual((first[101]['title'], first[102]['title']), ('Western Show', 'Flaky Show'))
        self.assertEqual(emby.json.call_count, 2)
        emby.json.side_effect = lambda method, path, body: [{'Name': '恢复后的中文名', 'ProviderIds': {'Tvdb': '102'}}]
        # After 6 minutes: 102 (transient failure) is retried, while 101 (confirmed absence) stays cached for 24h.
        with patch('portal.server.time.time', return_value=1360):
            second = app.localize({'token': 't'}, series)
        self.assertEqual(emby.json.call_count, 3)
        self.assertEqual((second[101]['title'], second[102]['title']), ('Western Show', '恢复后的中文名'))

    def test_tasks_only_localizes_series_referenced_by_active_work_and_retry_upload_guards_missing_db(self):
        with tempfile.TemporaryDirectory() as temp:
            app = App(Path(temp))
            with self.assertRaisesRegex(ValueError, '刷新任务列表'):
                app.retry_upload({'id': 'missing'})
            self.assertFalse((app.data / 'uploads.sqlite3').exists())
            qb, sonarr = Mock(), Mock()
            qb.json.side_effect = lambda method, path: (
                [{'hash': 'h1', 'name': 'S1', 'category': 'sonarr-tv', 'progress': 0.5, 'state': 'downloading',
                  'dlspeed': 1024, 'size': 2048, 'save_path': '/media/downloads/tv', 'added_on': 1}]
                if path.endswith('/info') else [])
            sonarr.json.side_effect = lambda method, path: (
                [{'id': 1, 'tvdbId': 10, 'title': 'Active'}, {'id': 2, 'tvdbId': 20, 'title': 'Idle'}]
                if path == '/api/v3/series' else {'page': 1, 'totalRecords': 1, 'records': [{'downloadId': 'h1', 'seriesId': 1, 'episodeId': 5}]})
            app.qb = lambda: qb
            app.client = lambda _: sonarr
            seen = []
            app.localize = lambda session, values: (seen.extend(v['tvdbId'] for v in values) or {10: {'title': '活动剧集'}})
            tasks = app.tasks({'token': 'admin'})
            self.assertEqual(seen, [10])
            self.assertEqual(tasks['items'][0]['name'], '活动剧集')

    def _admin(self, app):
        emby = Mock()
        emby.json.side_effect = lambda method, path, *args: (
            {'AccessToken': 'tok', 'User': {'Id': 'admin', 'Policy': {'IsAdministrator': True}}}
            if 'Authenticate' in path else {'Policy': {'IsAdministrator': True}})
        app.client = lambda _: emby
        _, call = self._serve(app)
        with patch('portal.server.emby_defaults'):
            _, login = call('login', {'username': 'admin', 'password': 'pw'})
        return emby, call, {'X-CSRF-Token': login['csrf']}

    def test_unexpected_errors_return_json_500_and_log_only_class_and_endpoint(self):
        import io
        from contextlib import redirect_stderr
        with tempfile.TemporaryDirectory() as temp:
            app = App(Path(temp))
            _, call, headers = self._admin(app)
            logged = io.StringIO()
            # e.g. an upstream returning an empty body where an object was expected.
            with patch.object(app, 'status', side_effect=AttributeError("'NoneType' has no attribute 'private-detail'")), \
                    redirect_stderr(logged):
                response, payload = call('status?q=private-term')
            self.assertEqual(response.code, 500)
            self.assertIn('服务处理请求时出错', payload['error'])
            self.assertNotIn('private', json.dumps(payload, ensure_ascii=False))
            self.assertEqual(logged.getvalue().strip(), 'portal GET /discover/api/status AttributeError')
            with patch.object(app, 'retry_upload', side_effect=RuntimeError('boom')), redirect_stderr(io.StringIO()):
                response, payload = call('retry-upload', {'id': 'x'}, headers)
            self.assertEqual(response.code, 500)
            # Known error types keep their existing 4xx handling.
            with patch.object(app, 'retry_upload', side_effect=ValueError('请刷新任务列表后重试。')):
                response, payload = call('retry-upload', {'id': 'x'}, headers)
            self.assertEqual((response.code, payload['error']), (400, '请刷新任务列表后重试。'))

    def test_openlist_body_that_is_not_an_object_is_a_cloud_error(self):
        client = Mock()
        for body in (None, [], 'ok'):
            client.json.return_value = body
            with self.assertRaisesRegex(ValueError, '云盘操作失败'):
                App.ol(client, 'GET', '/api/admin/storage/list')

    def test_login_reports_an_emby_outage_instead_of_blaming_the_input(self):
        with tempfile.TemporaryDirectory() as temp:
            app = App(Path(temp))
            emby = Mock()
            app.client = lambda _: emby
            emby.json.side_effect = APIError(502, '/Users/AuthenticateByName 返回 HTTP 502；请检查应用设置和登录。')
            with self.assertRaisesRegex(ValueError, r'^登录失败：Emby 暂时没有正常响应（HTTP 502）'):
                app.login({'username': 'admin', 'password': 'pw'})
            emby.json.side_effect = ValueError('本地应用 Emby 暂时无法连接或响应超时；请稍后重试。')
            with self.assertRaisesRegex(ValueError, r'^登录失败：暂时无法连接 Emby'):
                app.login({'username': 'admin', 'password': 'pw'})
            self.assertEqual(app.sessions, {})

    def test_series_list_distinguishes_an_unchosen_rule_from_a_broken_one(self):
        with tempfile.TemporaryDirectory() as temp:
            app = App(Path(temp))
            emby, call, _ = self._admin(app)
            sonarr = Mock()
            listed = [{'id': 1, 'tvdbId': 101, 'title': 'New', 'tags': []}, {'id': 2, 'tvdbId': 102, 'title': 'Changed', 'tags': []}]
            sonarr.json.side_effect = lambda method, path, *args: {'/api/v3/series': listed}.get(path, [])
            app.client = lambda name: emby if name == 'emby' else sonarr
            # 102 has a saved rule whose Sonarr profile and tag are gone; 101 never had one.
            (Path(temp) / 'versions.json').write_text(json.dumps({'102': {
                'fields': {'group': 'GRP'}, 'pattern': '/^(?=.*GRP).*$/i', 'profile_id': 9, 'tag_id': 3}}))
            _, payload = call('series')
            versions = {item['tvdbId']: item['version'] for item in payload['items']}
            self.assertEqual(versions, {101: {'valid': False, 'reason': 'missing'}, 102: {'valid': False, 'reason': 'invalid'}})

    def test_loopback_forwarded_for_skips_gateway_dns_and_aborted_reply_does_not_double_fault(self):
        with tempfile.TemporaryDirectory() as temp:
            app = App(Path(temp))
            emby = Mock()
            emby.json.side_effect = lambda method, path, *args: (
                {'AccessToken': 'tok', 'User': {'Id': 'admin', 'Policy': {'IsAdministrator': True}}}
                if 'Authenticate' in path else {'Policy': {'IsAdministrator': True}})
            app.client = lambda _: emby
            _, call = self._serve(app)
            with patch('portal.server.emby_defaults'), \
                    patch('portal.server.socket.gethostbyname_ex', side_effect=AssertionError('should short-circuit')) as dns:
                _, login = call('login', {'username': 'admin', 'password': 'pw'}, {'X-Forwarded-For': '198.51.100.9'})
            dns.assert_not_called()
            self.assertEqual(login['username'], 'admin')
            self.assertIn(('198.51.100.9', 'admin'), app.attempts)
            dummy = Mock()
            dummy.headers = {}
            dummy.wfile.write.side_effect = ConnectionAbortedError('client gone')
            Handler.reply(dummy, {'ok': True})  # Must swallow mid-write client abort cleanly.


if __name__ == '__main__':
    unittest.main()
