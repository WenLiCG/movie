import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock
from urllib.parse import parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'uploader'))
from uploader import Uploader, candidate
from cleanup import Cleaner


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.media = self.root / 'media'
        self.path = self.media / 'downloads/movies/film.mkv'
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b'archived video')
        self.remote = Mock()
        self.remote.find.return_value = {'is_dir': False, 'size': self.path.stat().st_size}
        self.uploader = Uploader(self.remote, self.root, self.media)
        self.job = candidate('movie:' + 'a' * 40 + ':0', self.path, '/aliyun/Movies/Film/film.mkv', self.path.stat().st_size)
        self.uploader.add(self.job)
        self.uploader.update(self.job, state='published', sha1=hashlib.sha1(self.path.read_bytes()).hexdigest())
        self.cleaner = Cleaner(self.uploader, self.root)
        self.torrent = {'hash': 'a' * 40, 'category': 'cinema-movies', 'progress': 1, 'amount_left': 0, 'added_on':900, 'completion_on': 1000, 'state': 'uploading', 'save_path': str(self.path.parent)}
        self.files = [{'name': 'film.mkv', 'size': self.path.stat().st_size, 'priority': 1, 'progress': 1}]
        self.torrents = [self.torrent]
        self.sources = Mock(media=self.media)
        self.sources.qb.json.side_effect = self.qb_json
        self.sources.qb.request.side_effect = self.qb_request
        self.deleted = []

    def tearDown(self):
        self.uploader.db.close()
        self.temp.cleanup()

    def qb_json(self, method, path, query=None):
        if path.endswith('/files'): return self.files
        if path.endswith('/info'):
            return [t for t in self.torrents if not query or t['hash'] == query.get('hashes')]
        raise AssertionError(path)

    def qb_request(self, method, path, body, headers):
        data = parse_qs(body.decode())
        if path.endswith('/stop'):
            self.torrent['state'] = 'stoppedUP'
        if path.endswith('/delete'):
            self.assertEqual(data['deleteFiles'], ['true'])
            self.deleted.append(data['hashes'][0])
            self.path.unlink()
            self.torrents.clear()
        return b'', []

    def run_cleanup(self, days=7):
        self.cleaner.run(self.sources, days, now=1000 + 8 * 86400)

    def test_default_age_and_disabled_rule_preserve_new_or_unpublished(self):
        self.run_cleanup(0)
        self.run_cleanup(9)
        self.assertTrue(self.path.exists())
        self.uploader.update(self.job, state='uploading')
        self.run_cleanup()
        self.assertTrue(self.path.exists())
        self.assertEqual(self.deleted, [])

    def test_only_verified_movie_removed_and_cloud_untouched(self):
        self.run_cleanup()
        self.assertFalse(self.path.exists())
        self.assertEqual(self.deleted, ['a' * 40])
        self.assertEqual(self.uploader.db.execute('SELECT state FROM cleanups').fetchone()[0], 'done')
        self.remote.find.assert_called()
        self.remote.move.assert_not_called()
        self.remote.call.assert_not_called()

    def test_manual_tv_cleanup_requires_published_tv_and_never_calls_sonarr(self):
        root=self.media/'downloads/tv-manual';root.mkdir(parents=True)
        target=root/self.path.name;self.path.rename(target);self.path=target
        self.torrent.update(category='cinema-tv',save_path=str(root))
        self.uploader.db.execute('DELETE FROM jobs');self.uploader.db.commit()
        job=candidate('manual-tv:'+self.torrent['hash']+':0',target,'/aliyun/TV/Variety/film.mkv',target.stat().st_size)
        self.uploader.add(job)
        self.run_cleanup()
        self.assertTrue(target.exists())
        self.uploader.update(job,state='published',sha1=hashlib.sha1(target.read_bytes()).hexdigest())
        self.run_cleanup()
        self.assertFalse(target.exists())
        self.sources.sonarr.json.assert_not_called()
        self.sources.sonarr.request.assert_not_called()
        self.remote.call.assert_not_called()

    def test_missing_cloud_changed_source_and_shared_payload_are_preserved(self):
        self.remote.find.return_value = None
        self.run_cleanup()
        self.assertTrue(self.path.exists())
        self.remote.find.return_value = {'is_dir': False, 'size': self.job['size']}
        self.path.write_bytes(b'changed!')
        self.run_cleanup()
        self.assertTrue(self.path.exists())
        self.path.write_bytes(b'archived video')
        self.torrents.append({**self.torrent, 'hash': 'b' * 40, 'category': 'other'})
        self.run_cleanup()
        self.assertTrue(self.path.exists())
        self.assertEqual(self.deleted, [])

    def test_persisted_plan_rechecks_settings_cloud_and_source(self):
        original = self.sources.qb.request.side_effect
        self.sources.qb.request.side_effect = OSError('temporary stop failure')
        self.run_cleanup()
        self.assertEqual(self.uploader.db.execute('SELECT state FROM cleanups').fetchone()[0], 'pending')
        self.sources.qb.request.side_effect = original
        self.run_cleanup(30)
        self.assertTrue(self.path.exists())
        self.path.write_bytes(b'modified after plan')
        self.run_cleanup()
        self.assertTrue(self.path.exists())
        self.assertEqual(self.deleted, [])

    def test_pending_plan_cannot_expire_a_new_download_or_override_keep(self):
        now = 1000 + 8*86400
        original = self.sources.qb.request.side_effect
        self.sources.qb.request.side_effect = OSError('temporary stop failure')
        self.cleaner.run(self.sources, now=now)
        self.sources.qb.request.side_effect = original
        self.torrent.update(added_on=now, completion_on=now)
        self.cleaner.run(self.sources, now=now+65)
        self.assertEqual(self.deleted, [])
        self.assertTrue(self.path.exists())

        self.sources.qb.request.side_effect = OSError('temporary stop failure')
        self.cleaner.run(self.sources, now=now+8*86400)
        payload = {'torrents':[self.torrent]}
        with self.uploader.db:
            self.uploader.db.execute("INSERT INTO task_actions VALUES ('movie','delete',?,'done',NULL,?)",
                                    (json.dumps(payload),now))
        self.sources.qb.request.side_effect = original
        self.cleaner.run(self.sources, now=now+8*86400+65)
        self.assertEqual(self.deleted, [])
        self.assertTrue(self.path.exists())

    def test_cleanup_reconciles_acknowledgement_lost_after_native_delete(self):
        now = 1000 + 8*86400
        request = self.sources.qb.request.side_effect
        def disconnect_after_delete(*args):
            result = request(*args)
            if args[1].endswith('/delete'):
                raise OSError('response lost')
            return result
        self.sources.qb.request.side_effect = disconnect_after_delete
        self.cleaner.run(self.sources, now=now)
        self.assertEqual(self.uploader.db.execute('SELECT state FROM cleanups').fetchone()[0], 'pending')
        self.cleaner.run(self.sources, now=now+65)
        self.assertEqual(self.uploader.db.execute('SELECT state FROM cleanups').fetchone()[0], 'done')
        self.assertEqual(self.deleted, [self.torrent['hash']])

    def test_tv_removes_hardlink_and_unmonitors_before_native_delete(self):
        tvroot = self.media / 'downloads/tv'
        tvroot.mkdir(parents=True)
        download = tvroot / 'episode.mkv'
        self.path.rename(download)
        self.path = download
        library = self.media / 'library/tv/Example/episode.mkv'
        library.parent.mkdir(parents=True)
        os.link(download, library)
        job = candidate('tv:42', library, '/aliyun/TV/Example/episode.mkv', library.stat().st_size)
        self.uploader.add(job)
        self.uploader.update(job, state='published', sha1=hashlib.sha1(library.read_bytes()).hexdigest())
        raw_sub=tvroot/'episode.zh-CN.srt';raw_sub.write_bytes(b'subtitle')
        library_sub=library.with_suffix('.srt');library_sub.write_bytes(raw_sub.read_bytes())
        subjob=candidate('tv-sub:42',library_sub,'/aliyun/TV/Example/episode.srt',8,
                         parent_id=job['id'],torrent_hash=self.torrent['hash'],download_source=str(raw_sub))
        self.uploader.add(subjob)
        self.uploader.update(subjob,state='published',sha1=hashlib.sha1(raw_sub.read_bytes()).hexdigest())
        self.remote.find.side_effect=lambda path: {'is_dir':False,'size':8 if path.endswith('.srt') else job['size']}
        self.files.append({'name':raw_sub.name,'size':8,'priority':1,'progress':1})
        original=self.sources.qb.request.side_effect
        def qb_request(*args):
            result=original(*args)
            if args[1].endswith('/delete'):raw_sub.unlink()
            return result
        self.sources.qb.request.side_effect=qb_request
        self.torrent.update(category='sonarr-tv', save_path=str(tvroot))
        self.files[0]['name'] = 'episode.mkv'
        calls = []
        native = {'id': 42, 'seriesId': 5, 'tvdbId':100, 'path': str(library), 'size': job['size']}
        self.sources.sonarr.json.side_effect = lambda method, path, *args, **kwargs: (calls.append((method, path)) or ([{'id': 10, 'episodeFileId': 42,'seasonNumber':1,'episodeNumber':1}] if path == '/api/v3/episode' else ([native] if path == '/api/v3/episodefile' else native)))
        def delete(method, path):
            calls.append((method, path))
            self.assertIn(('PUT', '/api/v3/episode/monitor'), calls)
            library.unlink()
            library_sub.unlink()
        self.sources.sonarr.request.side_effect = delete
        self.run_cleanup()
        self.assertFalse(library.exists())
        self.assertFalse(download.exists())
        self.assertFalse(raw_sub.exists())
        self.assertFalse(library_sub.exists())
        from archive import archived_keys
        self.assertEqual(archived_keys(self.root/'uploads.sqlite3',100),{(1,1)})
        self.assertEqual(self.deleted, ['a' * 40])


if __name__ == '__main__':
    unittest.main()
