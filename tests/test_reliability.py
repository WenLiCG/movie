"""Recovery, archive exclusions and disk ownership regression checks."""
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from archive import archived_keys, apply_scope, video, subtitle_for, space_required
import test_uploader
from test_versions import Sonarr
from portal.server import App
from uploader import disk_guard, Sources


class RecoveryTests(unittest.TestCase):
    setUp = test_uploader.UploaderTests.setUp
    tearDown = test_uploader.UploaderTests.tearDown
    row = test_uploader.UploaderTests.row
    run_upload = test_uploader.UploaderTests.run_upload

    def test_transitional_states_do_not_retransmit_or_publish(self):
        for state in (0,1,3,5,6,8,9):
            self.service.task_state = state
            self.run_upload()
            self.assertEqual(self.row()['state'], 'queued')
            self.assertIsNone(self.row()['receipt'])
        self.assertEqual(self.service.puts, 1)
        self.assertEqual(self.service.moves, 0)

    def test_cancel_requires_explicit_retry_and_new_stage(self):
        self.service.task_state = 4
        self.run_upload()
        old = self.row()
        self.run_upload()
        self.assertEqual(self.service.puts, 1)
        self.assertEqual(old['state'], 'failed')
        self.uploader.update(old, retry_requested=1)
        self.run_upload()
        self.service.task_state = 2
        self.run_upload()
        self.assertEqual(self.row()['state'], 'published')
        self.assertNotEqual(old['stage'], self.row()['stage'])
        self.assertIn(old['stage'], self.service.objects)

    def test_failed_tasks_have_a_bounded_retry_budget(self):
        self.service.task_state = 7
        for _ in range(6):
            self.uploader.db.execute('UPDATE jobs SET retry_at=0')
            self.uploader.db.commit()
            self.run_upload()
        self.assertEqual(self.service.puts, 5)
        self.assertEqual(self.row()['state'], 'failed')
        self.assertEqual(self.row()['error'], 'upload_retry_exhausted')

    def test_unknown_task_with_only_size_is_never_published(self):
        self.service.task_state = 1
        self.run_upload()
        job = self.row()
        self.uploader.update(job, state='uploading', task_id=None)
        self.remote.tasks = lambda: []
        self.service.objects[job['stage']].pop('hash_info')
        self.run_upload()
        self.assertEqual(self.row()['error'], 'upload_unconfirmed')
        self.assertEqual(self.service.puts, 1)
        self.assertEqual(self.service.moves, 0)

    def test_strong_evidence_recovers_lost_put_response(self):
        self.service.task_state = 1
        self.run_upload()
        job = self.row()
        self.uploader.update(job, state='uploading', task_id=None)
        self.remote.tasks = lambda: []
        self.run_upload()
        self.assertEqual(self.row()['state'], 'published')
        self.assertEqual(self.service.puts, 1)

    def test_cloud_episode_survives_local_deletion_and_excludes_backfill(self):
        output=self.run_upload()
        self.assertEqual(self.row()['state'],'published',(self.row()['error'],output))
        job = self.row()
        self.uploader.update(job, tvdb_id=42, episode_keys='[[1,1],[1,2]]')
        self.source.unlink()
        keys = archived_keys(self.state/'uploads.sqlite3', 42)
        self.assertEqual(keys, {(1,1),(1,2)})
        sonarr = Sonarr()
        apply_scope(sonarr, sonarr.series[0], 'all', None, keys)
        commands = [b for m,p,b in sonarr.calls if p=='/api/v3/episode/monitor']
        self.assertIn({'episodeIds':[101],'monitored':False}, commands)
        self.assertIn({'episodeIds':[201],'monitored':True}, commands)
        self.assertEqual(sonarr.calls[-1][2], {'name':'MissingEpisodeSearch','seriesId':9,'monitored':True})


class BoundaryTests(unittest.TestCase):
    def test_sonarr_renamed_copied_subtitle_is_linked_by_content(self):
        with tempfile.TemporaryDirectory() as temp:
            media=Path(temp);raw=media/'downloads/tv/Pack';library=media/'library/tv/Show'
            raw.mkdir(parents=True);library.mkdir(parents=True)
            (raw/'episode.mkv').write_bytes(b'video')
            import os
            os.link(raw/'episode.mkv',library/'episode.mkv')
            (raw/'episode.zh-CN.srt').write_bytes(b'subtitle')
            (library/'episode.srt').write_bytes(b'subtitle')
            sources=Sources({'qb_url':'http://localhost','sonarr_url':'http://localhost','sonarr_api_key':'test'},media)
            sources.qb=Mock();sources.sonarr=Mock()
            torrent={'hash':'a'*40,'category':'sonarr-tv','progress':1,'save_path':str(raw)}
            files=[{'name':n,'priority':1,'progress':1,'size':(raw/n).stat().st_size} for n in ('episode.mkv','episode.zh-CN.srt')]
            sources.qb.json.side_effect=lambda m,p,**k: [torrent] if p.endswith('/info') else files
            series={'id':9,'tvdbId':42,'path':str(library)}
            episode={'id':7,'seriesId':9,'relativePath':'episode.mkv','path':str(library/'episode.mkv'),'size':5}
            sources.sonarr.json.side_effect=lambda m,p,**k: [series] if p.endswith('/series') else [episode] if p.endswith('/episodefile') else [{'episodeFileId':7,'seasonNumber':1,'episodeNumber':1}]
            candidates=sources.tv()
            self.assertEqual(len(candidates),2)
            sub=next(v for v in candidates if v['parent_id'])
            self.assertEqual(sub['download_source'],str(raw/'episode.zh-CN.srt'))
            (library/'episode.srt').write_bytes(b'changed!')
            self.assertEqual(sources.tv(),[])

    def test_whole_season_batches_native_commands_without_changing_monitoring(self):
        with tempfile.TemporaryDirectory() as temp:
            app=App(Path(temp));native=Mock();app.client=lambda _:native
            app.require_version=Mock()
            episodes=[{'id':n,'seasonNumber':1,'episodeNumber':n,'hasFile':False} for n in range(1,206)]
            native.json.side_effect=lambda m,p,b=None: ({'tvdbId':42} if p=='/api/v3/series/9' else {'records':[]} if '/queue?' in p else episodes if '/episode?' in p else {})
            app.download_episodes({'series_id':9,'episode_ids':list(range(1,206))})
            commands=[c.args[2]['episodeIds'] for c in native.json.call_args_list if c.args[0]=='POST']
            self.assertEqual(list(map(len,commands)),[100,100,5])
            self.assertFalse(any(c.args[0]=='PUT' for c in native.json.call_args_list))

    def test_media_rules_keep_short_videos_and_language_subtitles(self):
        for name in ('Show.mkv','sampled.mkv','Show.S01E01.mkv'):
            self.assertTrue(video(name))
        for name in ('Show.sample.mkv','Sample/clip.mkv','BDMV/STREAM/00001.m2ts','disk.iso'):
            self.assertFalse(video(name))
        self.assertTrue(subtitle_for('Show.zh-CN.forced.ass','Show.mkv'))
        self.assertFalse(subtitle_for('Show2.srt','Show.mkv'))
        self.assertFalse(subtitle_for('other/Show.srt','Show.mkv'))

    def test_disk_formula_counts_pending_upload_and_existing_downloads(self):
        gib=1024**3
        self.assertEqual(space_required(2*gib,3*gib,4*gib,60*gib),14*gib)
        with tempfile.TemporaryDirectory() as temp:
            app=App(Path(temp))
            qb=Mock();qb.json.return_value=[{'category':'cinema-tv','amount_left':3*gib,'state':'downloading'}]
            app.qb=lambda:qb
            with patch('portal.server.shutil.disk_usage', return_value=Mock(total=60*gib,free=10*gib)):
                with self.assertRaisesRegex(ValueError,'需要 12.0 GB，当前可用 10.0 GB'):
                    app.check_space(2*gib)

    def test_disk_guard_only_resumes_its_own_pause(self):
        with tempfile.TemporaryDirectory() as temp:
            sources=Mock(media=Path(temp));qb=sources.qb
            owned={'hash':'a','category':'cinema-tv','state':'downloading','progress':.2,'amount_left':8*1024**3,'size':10*1024**3,'save_path':'/media/downloads/tv-manual','added_on':1}
            manual={**owned,'hash':'b','state':'stoppedDL'}
            qb.json.return_value=[owned,manual]
            with patch('uploader.shutil.disk_usage',return_value=Mock(total=60*1024**3,free=6*1024**3)):
                self.assertEqual(disk_guard(sources,Path(temp)),1)
            self.assertIn('a',json.loads((Path(temp)/'disk-paused.json').read_text()))
            owned['state']='stoppedDL'
            with patch('uploader.shutil.disk_usage',return_value=Mock(total=60*1024**3,free=40*1024**3)):
                self.assertEqual(disk_guard(sources,Path(temp)),0)
            calls=[c.args[2].decode() for c in qb.request.call_args_list]
            self.assertEqual(calls,['hashes=a','hashes=a'])

    def test_login_limit_is_per_source_and_session_has_no_password(self):
        with tempfile.TemporaryDirectory() as temp:
            app=App(Path(temp));native=Mock()
            native.json.return_value={'AccessToken':'test-token','User':{'Id':'1','Policy':{'IsAdministrator':True}}}
            app.client=lambda _:native;app.metadata_defaults=Mock()
            for _ in range(8):app.login({'username':'admin','password':'secret'},'192.0.2.1')
            with self.assertRaises(ValueError):app.login({'username':'admin','password':'secret'},'192.0.2.1')
            _,session=app.login({'username':'admin','password':'different'},'192.0.2.2')
            self.assertNotIn('password',session)

    def test_login_rate_window_discards_individual_old_attempts(self):
        with tempfile.TemporaryDirectory() as temp:
            app=App(Path(temp));native=Mock()
            native.json.return_value={'AccessToken':'test-token','User':{'Id':'1','Policy':{'IsAdministrator':True}}}
            app.client=lambda _:native;app.metadata_defaults=Mock()
            for now in range(0,110,11):
                with patch('portal.server.time.time',return_value=now):
                    app.login({'username':'admin','password':'secret'},'192.0.2.1')


if __name__=='__main__':
    unittest.main()
