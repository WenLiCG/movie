"""Independent QA for the imported movie CLI review candidate.

Added after tree 1d28e35916c3620d192f5d9e35e19e2c83ad1680 was imported.
These tests do not change product behavior. They stress cleanup data-loss
boundaries, playback-evidence authorization, stale logout, and uploader
backoff. Characterization tests record edges the candidate still allows.
"""
import json
import tempfile
import threading
import unittest
from http.cookiejar import CookieJar
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

from integration import APIError
from portal.playback import playback_records
from portal.server import App, Handler


def _load_uploader_module():
    """Prefer uploader/uploader.py over the repo directory if both are visible."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'uploader'))
    loaded = sys.modules.get('uploader')
    if loaded is not None and not hasattr(loaded, 'Uploader'):
        del sys.modules['uploader']


def _cleanup_tests():
    # Import inside the helper so discovery does not collect CleanupTests twice.
    _load_uploader_module()
    from test_cleanup import CleanupTests
    return CleanupTests


class IndependentCleanupBoundaries(unittest.TestCase):
    def setUp(self):
        _cleanup_tests().setUp(self)

    def tearDown(self):
        _cleanup_tests().tearDown(self)

    def qb_json(self, method, path, query=None):
        return _cleanup_tests().qb_json(self, method, path, query)

    def qb_request(self, method, path, body, headers):
        return _cleanup_tests().qb_request(self, method, path, body, headers)

    def run_cleanup(self, days=7):
        return _cleanup_tests().run_cleanup(self, days)

    def _reset(self):
        self.deleted.clear()
        self.uploader.db.execute('DELETE FROM cleanups')
        self.uploader.db.commit()

    def _extra(self, name, payload=b'sidecar'):
        path = self.path.parent / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path

    def _restore_published_main(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_bytes(b'archived video')
        if self.torrent not in self.torrents:
            self.torrents.append(self.torrent)

    def _with_sidecar(self, name, **file_fields):
        self._restore_published_main()
        extra = self._extra(name)
        sidecar = {'name': name, 'size': extra.stat().st_size, 'priority': 1, 'progress': 1}
        sidecar.update(file_fields)
        self.files = [
            {'name': 'film.mkv', 'size': self.path.stat().st_size, 'priority': 1, 'progress': 1},
            sidecar,
        ]
        self._reset()
        self.run_cleanup()
        return extra

    def test_alternate_sidecars_block_cleanup_of_a_published_main_video(self):
        for name in ('Film.SUP', 'Subs/Film.idx', 'Subs/Film.SUB', 'Film.vtt',
                     'Audio/Track.FLAC', 'Extra.m2t', 'Sample.People.mka', 'Film.EAC3'):
            extra = self._with_sidecar(name)
            self.assertEqual(self.deleted, [], name)
            self.assertTrue(extra.exists(), name)
            self.assertTrue(self.path.exists(), name)
            self.assertEqual(self.uploader.db.execute('SELECT state, reason FROM cleanups').fetchone()[:],
                             ('deferred', 'cleanup_unarchived_media'), name)
            extra.unlink()

    def test_unpublished_text_subtitle_still_blocks_cleanup(self):
        self._with_sidecar('film.zh.srt', size=12)
        self.assertEqual(self.deleted, [])
        self.assertTrue(self.path.exists())
        self.assertEqual(self.uploader.db.execute('SELECT reason FROM cleanups').fetchone()[0], 'cleanup_not_archived')

    def test_iso_is_retained_under_the_unarchived_media_reason(self):
        self._with_sidecar('disk.iso')
        self.assertEqual(self.deleted, [])
        self.assertEqual(self.uploader.db.execute('SELECT state, reason FROM cleanups').fetchone()[:],
                         ('deferred', 'cleanup_unarchived_media'))

    def test_incomplete_or_present_unselected_sidecar_does_not_delete_the_torrent(self):
        self._with_sidecar('film.sup', progress=0.5)
        self.assertEqual(self.deleted, [])
        self.assertIsNone(self.uploader.db.execute('SELECT state FROM cleanups').fetchone())
        self._with_sidecar('film.mka', priority=0, progress=0)
        self.assertEqual(self.deleted, [])
        self.assertIsNone(self.uploader.db.execute('SELECT state FROM cleanups').fetchone())

    def test_sample_named_video_uses_an_exclusive_one_gib_qb_size_threshold(self):
        kept = self._extra('Sample.Feature.mkv', b'on-disk-bytes-are-not-the-threshold')
        self.files = [
            {'name': 'film.mkv', 'size': self.path.stat().st_size, 'priority': 1, 'progress': 1},
            {'name': kept.name, 'size': 1024 ** 3 + 1, 'priority': 1, 'progress': 1},
        ]
        self._reset()
        self.run_cleanup()
        self.assertEqual(self.deleted, [])
        self.assertEqual(self.uploader.db.execute('SELECT reason FROM cleanups').fetchone()[0], 'cleanup_not_archived')

        self.files[1]['size'] = 1024 ** 3
        self._reset()
        self.run_cleanup()
        self.assertEqual(self.deleted, ['a' * 40])

    def test_missing_qb_size_fails_closed_instead_of_deleting(self):
        # qB file entries are expected to carry size. Omitting it raises while comparing the plan,
        # so the torrent stays on disk with a generic deferred reason rather than being removed.
        self._extra('Movie.sample.mkv', b'local file whose qB size was omitted')
        self.files = [
            {'name': 'film.mkv', 'size': self.path.stat().st_size, 'priority': 1, 'progress': 1},
            {'name': 'Movie.sample.mkv', 'priority': 1, 'progress': 1},
        ]
        self._reset()
        self.run_cleanup()
        self.assertEqual(self.deleted, [])
        self.assertTrue(self.path.exists())
        self.assertEqual(self.uploader.db.execute('SELECT state, reason FROM cleanups').fetchone()[:],
                         ('pending', 'cleanup_check_failed'))

    def test_small_sample_named_video_is_still_deleted_with_the_published_main(self):
        self._with_sidecar('Feature.sample.mkv', size=10 * 1024 * 1024)
        self.assertEqual(self.deleted, ['a' * 40])

    def test_sidecar_extensions_outside_the_allow_list_are_still_deleted(self):
        # Characterization, not acceptance: .mks/.ttml are subtitle sidecars the allow-list misses.
        for name in ('film.mks', 'film.ttml'):
            self._with_sidecar(name)
            self.assertEqual(self.deleted, ['a' * 40], name)


class IndependentEvidenceBoundaries(unittest.TestCase):
    def event(self, kind='media', **fields):
        return {'at': 900, 'actor': 'a' * 24, 'item': '9', 'kind': kind, 'status': 200, **fields}

    def read(self, events, now=1000):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / 'events.jsonl').write_text('\n'.join(json.dumps(event) for event in events) + '\n', encoding='utf-8')
            return playback_records(temp, now=now)

    def test_denied_session_statuses_cannot_create_or_displace_evidence(self):
        real = self.event(status=403, delivery='failed', at=100, actor='b' * 24)
        denied = []
        for status in (199, 300, 401, 500):
            denied.extend(self.event('start', at=200 + status * 10 + i, actor=f'{(status * 100 + i):024x}', status=status)
                          for i in range(40))
        accepted = self.event('progress', at=980, status=204, session='accepted', position=10 ** 7, actor='c' * 24)
        only_denied = self.read(denied)
        self.assertEqual(only_denied, [])
        records = self.read([real, *denied, accepted])
        self.assertEqual(len(records), 2)
        self.assertEqual(sorted(item['error_status'] for item in records), [0, 403])
        self.assertTrue(any(item['has_progress'] for item in records))

    def test_historical_media_rows_with_failure_status_still_fill_the_admin_list(self):
        # The portal drops rejected session reports. Old media rows are not classified the same way.
        forged = [self.event(at=800 + i, actor=f'{i:024x}', status=401, delivery='failed') for i in range(120)]
        records = self.read(forged, now=2000)
        self.assertEqual(len(records), 100)
        self.assertTrue(all(item['state'] == 'failed' and item['error_status'] == 401 for item in records))


class IndependentSessionBoundaries(unittest.TestCase):
    def serve(self, app):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.app = app
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), worker.join(timeout=5)))
        base = 'http://127.0.0.1:' + str(server.server_port)
        opener = build_opener(HTTPCookieProcessor(CookieJar()))

        def call(path, body=None, headers=None):
            request = Request(base + '/discover/api/' + path, None if body is None else json.dumps(body).encode(),
                              {'Content-Type': 'application/json', 'Origin': base, **(headers or {})})
            try:
                with opener.open(request, timeout=5) as response:
                    return response.status, dict(response.headers), json.loads(response.read() or b'{}')
            except HTTPError as error:
                payload = json.loads(error.read() or b'{}')
                error.close()
                return error.code, dict(error.headers), payload

        return call

    def login(self, emby):
        app = App(Path(tempfile.mkdtemp()))
        emby.json.side_effect = lambda method, path, *args: (
            {'AccessToken': 'private-emby-token', 'User': {'Id': 'admin', 'Policy': {'IsAdministrator': True}}}
            if 'Authenticate' in path else {'Policy': {'IsAdministrator': True}})
        app.client = lambda name: emby
        call = self.serve(app)
        with patch('portal.server.emby_defaults'):
            status, _, login = call('login', {'username': 'admin', 'password': 'test'})
        self.assertEqual(status, 200, login)
        return app, call, login

    def test_logout_clears_a_live_cookie_when_emby_logout_raises_api_or_timeout(self):
        for error in (APIError(503, 'emby down'), TimeoutError('timed out')):
            emby = Mock()
            app, call, login = self.login(emby)
            emby.json.side_effect = APIError(503, 'admin check down')
            emby.request.side_effect = error
            status, headers, _ = call('logout', {}, {'X-CSRF-Token': login['csrf']})
            self.assertEqual(status, 200, type(error).__name__)
            self.assertIn('Max-Age=0', headers['Set-Cookie'])
            self.assertEqual(app.sessions, {})
            status, _, payload = call('status')
            self.assertEqual(status, 401, type(error).__name__)
            self.assertNotIn('private-emby-token', json.dumps(payload))

    def test_wrong_csrf_does_not_revoke_the_session(self):
        emby = Mock()
        app, call, login = self.login(emby)
        status, _, _ = call('logout', {}, {'X-CSRF-Token': 'x' * len(login['csrf'])})
        self.assertEqual(status, 403)
        self.assertEqual(len(app.sessions), 1)
        self.assertIn(login['csrf'], {session['csrf'] for session in app.sessions.values()})


class IndependentBackoffSafety(unittest.TestCase):
    def test_delay_boundary_cap_and_unrelated_action(self):
        with tempfile.TemporaryDirectory() as temp:
            from archive import create_actions
            _load_uploader_module()
            from uploader import Failure, Uploader
            uploader = Uploader(Mock(), Path(temp), Path(temp) / 'media')
            try:
                now = [1000.0]
                uploader.clock = lambda: now[0]
                with uploader.db:
                    create_actions(uploader.db)
                    uploader.db.execute("INSERT INTO task_actions VALUES ('torrent:x','delete','{}','pending',NULL,1)")
                    uploader.db.execute("INSERT INTO task_actions VALUES ('torrent:y','cancel','{}','pending',NULL,1)")
                calls = []

                def waiting(sources, action):
                    calls.append(action['id'])
                    raise Failure('delete_all_waiting_scan')

                uploader.process_action = waiting
                for _ in range(4):
                    uploader.process_actions(None)
                self.assertEqual(calls.count('torrent:x'), 4)
                self.assertEqual(calls.count('torrent:y'), 4)
                now[0] += 9
                uploader.process_actions(None)
                self.assertEqual(calls.count('torrent:x'), 4)
                now[0] += 1
                uploader.process_actions(None)
                self.assertEqual(calls.count('torrent:x'), 5)
                key = ('torrent:x', 1)
                for _ in range(8):
                    scheduled = uploader.action_backoff[key][1]
                    now[0] = scheduled
                    uploader.process_actions(None)
                delay = uploader.action_backoff[key][1] - now[0]
                self.assertEqual(delay, 300)
                self.assertEqual(uploader.db.execute("SELECT state FROM task_actions WHERE id='torrent:x'").fetchone()[0], 'pending')
            finally:
                uploader.db.close()


if __name__ == '__main__':
    unittest.main()
