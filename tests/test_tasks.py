"""Project grouping, scoped controls and explicit local deletion boundaries."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs,urlsplit

from archive import archived_keys,task_actions
from portal.server import App
import test_uploader
from uploader import Uploader,candidate


class QB:
    def __init__(self,root):
        self.items=[];self.files={};self.calls=[]
        for n in (1,2,3):
            digest=str(n)*40
            self.items.append({'hash':digest,'name':'Show' if n<3 else 'Movie','category':'sonarr-tv' if n<3 else 'cinema-movies',
                               'save_path':str(root/'downloads'/('tv' if n<3 else 'movies')),'added_on':n,'progress':1 if n!=2 else .5,
                               'size':10,'state':'uploading' if n!=2 else 'downloading','dlspeed':0})
            self.files[digest]=[{'index':0,'name':str(n)+'.mkv','size':10,'priority':1,'progress':1 if n!=2 else .5}]
            folder=Path(self.items[-1]['save_path']);folder.mkdir(parents=True,exist_ok=True);(folder/(str(n)+'.mkv')).write_bytes(b'frame-data')

    def json(self,method,path,query=None):
        parsed=urlsplit(path);params=query or {k:v[0] for k,v in parse_qs(parsed.query).items()}
        if parsed.path.endswith('/info'):
            return copy.deepcopy([t for t in self.items if not params.get('hashes') or t['hash']==params['hashes']])
        if parsed.path.endswith('/files'):return copy.deepcopy(self.files[params['hash']])
        raise AssertionError(path)

    def request(self,method,path,body=None,headers=None,form=None):
        values=form or {k:v[0] for k,v in parse_qs(body.decode()).items()};self.calls.append((path,values.copy()))
        t=next(t for t in self.items if t['hash']==values['hashes'])
        if path.endswith('/stop'):t['state']='stoppedUP' if t['progress']==1 else 'stoppedDL'
        elif path.endswith('/start'):t['state']='uploading' if t['progress']==1 else 'downloading'
        elif path.endswith('/delete'):
            if values['deleteFiles']=='true':
                for file in self.files[t['hash']]:
                    p=Path(t['save_path'])/file['name']
                    if p.exists():p.unlink()
            self.items.remove(t)
        else:raise AssertionError(path)
        return b'Ok.'


class Sonarr:
    def __init__(self,root):
        self.series=[{'id':9,'tvdbId':42,'path':str(root/'library/tv/Show'),'title':'Show','monitored':True,'tags':[]},
                     {'id':10,'tvdbId':99,'title':'Show','monitored':False,'tags':[]}]
        self.queue=[{'id':n,'downloadId':str(n)*40,'seriesId':9,'episodeId':100+n} for n in (1,2)]
        self.monitored_episodes = {101:True,102:True}
        self.tags=[];self.calls=[];self.busy=[];self.files=[]
        for n in (1,2):
            p=root/'library/tv/Show'/(str(n)+'.mkv');p.parent.mkdir(parents=True,exist_ok=True)
            os.link(root/'downloads/tv'/(str(n)+'.mkv'),p)
            self.files.append({'id':n,'seriesId':9,'path':str(p),'size':10})

    def json(self,method,path,body=None,query=None):
        route=urlsplit(path).path;self.calls.append((method,route,copy.deepcopy(body)))
        if route=='/api/v3/series':return copy.deepcopy(self.series)
        if route.startswith('/api/v3/series/'):
            value=next(s for s in self.series if s['id']==int(route.split('/')[-1]))
            if method=='PUT':value.update(copy.deepcopy(body))
            return copy.deepcopy(value)
        if route=='/api/v3/queue':return {'records':copy.deepcopy(self.queue),'totalRecords':len(self.queue)}
        if route=='/api/v3/command':return self.busy
        if route=='/api/v3/tag':
            if method=='POST':self.tags.append({**body,'id':1})
            return self.tags[-1] if method=='POST' else self.tags
        if route=='/api/v3/episodefile':return copy.deepcopy(self.files)
        if route=='/api/v3/episode':return [{'id':i,'episodeFileId':i-100,'seasonNumber':1,'episodeNumber':i-100,
                                              'monitored':flag} for i,flag in self.monitored_episodes.items()]
        if route=='/api/v3/episode/monitor':
            self.monitored_episodes.update(dict.fromkeys(body['episodeIds'], body['monitored']))
            return {}
        raise AssertionError((method,path))

    def request(self,method,path):
        self.calls.append((method,path,None));file=next(f for f in self.files if f['id']==int(path.split('/')[-1]))
        Path(file['path']).unlink();self.files.remove(file)


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.media=self.root/'media'
        self.state=self.root/'state';(self.state/'data').mkdir(parents=True)
        self.qb=QB(self.media);self.native=Sonarr(self.media);self.remote=Mock()
        self.uploader=Uploader(self.remote,self.state/'data',self.media)
        self.app=App(self.state);self.app.qb=lambda:self.qb;self.app.client=lambda _:self.native
        self.app.require_version=Mock(return_value={})
        self.sources=SimpleNamespace(qb=self.qb,sonarr=self.native,media=self.media)
        self.jobs=[]
        for n in (1,2):
            source=self.media/'library/tv/Show'/(str(n)+'.mkv')
            job=candidate('tv:'+str(n),source,'/aliyun/TV/Show/'+source.name,10,tvdb_id=42,episode_keys=json.dumps([[1,n]]),torrent_hash=str(n)*40,added_on=n,download_source=str(self.media/'downloads/tv'/source.name))
            self.uploader.add(job)
            if n==1:self.uploader.update(job,state='published',sha1=hashlib.sha1(source.read_bytes()).hexdigest())
            self.jobs.append(job)

    def tearDown(self):
        self.uploader.db.close();self.temp.cleanup()

    def run_action(self,action,delete_files=False,key='series:42'):
        body = {'id':key,'action':action,'delete_files':delete_files}
        if delete_files:
            body['scope'] = self.app.task_action({}, {**body, 'preview':True})['scope']
        return self.app.task_action({}, body)

    def test_same_series_groups_resources_and_other_movie_remains_separate(self):
        groups=self.app.tasks()['items'];self.assertEqual(len(groups),2)
        show=next(g for g in groups if g['id']=='series:42')
        self.assertEqual(len(show['resources']),2);self.assertEqual(show['file_count'],2)
        self.assertEqual(len(show['uploads']),2)

    def test_file_links_use_either_source_and_keep_the_first_match(self):
        torrent = self.qb.items[0]
        folder = Path(torrent['save_path'])
        self.qb.files[torrent['hash']].append({'index':1, 'name':'1.srt', 'size':2, 'priority':1, 'progress':1})
        with self.uploader.db:
            self.uploader.db.execute(
                'INSERT INTO jobs (id,source,dest,size,download_source,torrent_hash) VALUES (?,?,?,?,?,?)',
                ('subtitle', str(folder/'1.srt'), '/aliyun/TV/Show/1.srt', 2, str(folder/'1.mkv'), torrent['hash']))
        show = next(g for g in self.app.tasks()['items'] if g['id'] == 'series:42')
        resource = next(r for r in show['resources'] if r['hash'] == torrent['hash'])
        self.assertEqual({f['name']:f['job_id'] for f in resource['files']},
                         {'1.mkv':self.jobs[0]['id'], '1.srt':'subtitle'})

    def test_task_reports_finished_transfer_and_rechecks_verification_without_reupload(self):
        job = self.jobs[1]
        self.uploader.update(job, state='queued', terminal_proven=1, error='remote_hash_mismatch', retry_at=999)
        group = next(g for g in self.app.tasks()['items'] if g['id']=='series:42')
        item = next(u for u in group['uploads'] if u['id']==job['id'])
        self.assertIs(item['terminal_proven'], True)
        self.app.retry_upload({'id':job['id']})
        row = self.uploader.db.execute('SELECT state,retry_requested,retry_at FROM jobs WHERE id=?',(job['id'],)).fetchone()
        self.assertEqual(tuple(row), ('queued',0,0))
        with self.uploader.db:
            self.uploader.db.execute('ALTER TABLE jobs DROP COLUMN terminal_proven')
        self.assertTrue(all(u['terminal_proven'] is False for u in self.app.uploads()))

    def test_completed_selected_video_waits_for_import_and_unselected_files_are_not_counted(self):
        files = self.qb.files[self.qb.items[0]['hash']]
        extra = {'index':1,'name':'new.mkv','size':10,'progress':1,'priority':1}
        files.extend([{**extra,'name':'Film.sample.mkv'}, {**extra,'name':'unselected.mkv','priority':0}])
        def show():
            return next(g for g in self.app.tasks()['items'] if g['id']=='series:42')
        self.assertEqual((show()['waiting_video'], show()['file_count']), (False, 3))
        files.append(extra)
        group = show()
        self.assertTrue(group['waiting_video'])
        self.assertTrue(group['resources'][0]['waiting_video'])
        self.assertEqual(group['file_count'], 4)
        roles = {f['name']: f['role'] for f in group['resources'][0]['files']}
        self.assertEqual(roles['new.mkv'], 'video')
        self.assertEqual(roles['Film.sample.mkv'], 'extra')

    def test_queue_shared_by_two_series_stays_out_of_project_actions(self):
        digest = self.qb.items[0]['hash']
        original = self.native.json
        def paged(method, path, body=None):
            if path.startswith('/api/v3/queue?'):
                page = int(parse_qs(urlsplit(path).query)['page'][0])
                batch = self.native.queue if page == 1 else [{'id':3,'downloadId':digest,'seriesId':10,'episodeId':200}]
                return {'records':batch, 'page':page, 'totalRecords':3}
            return original(method, path, body)
        self.native.json = paged
        groups = {g['id']:g for g in self.app.tasks()['items']}
        self.assertIn('torrent:'+digest, groups)
        self.assertIn(200, self.app.queue_episode_ids(self.native))
        self.run_action('pause')
        self.assertEqual(self.qb.items[0]['state'], 'uploading')
        self.assertEqual(self.qb.items[1]['state'], 'stoppedDL')

    def test_incomplete_queue_cannot_pause_a_project(self):
        original = self.native.json
        def incomplete(method, path, body=None):
            if path.startswith('/api/v3/queue?'):
                page = int(parse_qs(urlsplit(path).query)['page'][0])
                return {'page':page, 'totalRecords':3, 'records':self.native.queue if page == 1 else []}
            return original(method, path, body)
        self.native.json = incomplete
        with self.assertRaisesRegex(ValueError, '队列'):
            self.run_action('pause')
        self.assertFalse(self.qb.calls)
        self.assertTrue(self.native.series[0]['monitored'])

    def test_overlapping_queue_pages_cannot_pause_a_project(self):
        original = self.native.json
        def overlapping(method, path, body=None):
            if path.startswith('/api/v3/queue?'):
                page = int(parse_qs(urlsplit(path).query)['page'][0])
                return {'page':page, 'totalRecords':3, 'records':self.native.queue if page == 1 else self.native.queue[1:]}
            return original(method, path, body)
        self.native.json = overlapping
        with self.assertRaisesRegex(ValueError, '队列'):
            self.run_action('pause')
        self.assertFalse(self.qb.calls)
        self.assertTrue(self.native.series[0]['monitored'])

    def test_pre_uploader_pause_allows_space_check_and_resume(self):
        self.app = App(self.root/'fresh-state')
        self.app.qb = lambda:self.qb
        self.app.client = lambda _:self.native
        key = 'torrent:'+self.qb.items[2]['hash']
        self.run_action('pause', key=key)
        with patch('portal.server.shutil.disk_usage',return_value=SimpleNamespace(free=10**12,total=10**12)):
            self.app.check_space(100)
        self.run_action('resume', key=key)
        self.assertEqual(self.qb.items[2]['state'], 'uploading')

    def test_pause_resume_and_disk_guard_do_not_undo_user_intent(self):
        path = self.state/'data/disk-paused.json'
        path.write_text(json.dumps({torrent['hash']:{'at':1} for torrent in self.qb.items}))
        self.run_action('pause')
        self.assertFalse(self.native.series[0]['monitored'])
        self.assertEqual(json.loads(path.read_text()), {'3'*40:{'at':1}})
        self.assertTrue(all(t['state'].startswith('stopped') for t in self.qb.items[:2]))
        self.run_action('resume')
        self.assertTrue(self.native.series[0]['monitored']);self.app.require_version.assert_called_once()
        self.assertEqual(self.qb.items[1]['state'],'downloading')

    def test_pause_failure_preserves_user_intent_and_reports_unconfirmed_control(self):
        path=self.state/'data/disk-paused.json';path.write_text(json.dumps({'2'*40:{'at':1}}))
        self.qb.request=Mock(side_effect=ValueError('upstream unavailable'))
        with self.assertRaisesRegex(ValueError,'尚未确认'):self.run_action('pause')
        self.assertEqual(json.loads(path.read_text()),{})
        self.assertEqual(task_actions(self.state/'data/uploads.sqlite3')[0]['error'],'task_control_failed')

    def test_failed_resume_retries_without_dropping_monitoring(self):
        self.run_action('pause')
        request = self.qb.request
        self.qb.request = Mock(side_effect=ValueError('upstream unavailable'))
        with self.assertRaisesRegex(ValueError, '尚未确认'):
            self.run_action('resume')
        self.qb.request = request
        self.run_action('resume')
        self.assertTrue(self.native.series[0]['monitored'])
        self.assertEqual(self.qb.items[1]['state'], 'downloading')

    def test_resume_after_native_pause_preserves_existing_monitoring(self):
        self.qb.items[1]['state'] = 'stoppedDL'
        self.run_action('resume')
        self.assertTrue(self.native.series[0]['monitored'])

    def test_cancel_retains_payload_and_can_continue_after_acknowledgement(self):
        self.run_action('cancel');self.uploader.process_actions(self.sources)
        self.assertEqual(len(self.qb.items),3)
        self.assertTrue(Path(self.jobs[1]['source']).exists())
        self.assertEqual(self.uploader.db.execute('SELECT state FROM jobs WHERE id=?',(self.jobs[1]['id'],)).fetchone()[0],'cancelled')
        self.run_action('resume')
        self.assertEqual(self.uploader.db.execute('SELECT state FROM jobs WHERE id=?',(self.jobs[1]['id'],)).fetchone()[0],'pending')

    def test_resume_includes_cancelled_job_outside_display_history(self):
        self.run_action('cancel')
        self.uploader.process_actions(self.sources)
        with self.uploader.db:
            self.uploader.db.executemany(
                "INSERT INTO jobs (id,source,dest,size,state,tvdb_id) VALUES (?, 'other', ?, 1, 'cancelled', 99)",
                ((str(i), f'/aliyun/TV/Other/{i}.mkv') for i in range(1000)))
        self.assertNotIn(self.jobs[1]['id'], {job['id'] for job in self.app.uploads()})
        self.run_action('resume')
        states = dict(self.uploader.db.execute('SELECT id,state FROM jobs'))
        self.assertEqual(states[self.jobs[1]['id']], 'pending')
        self.assertEqual(states['0'], 'cancelled')

    def test_remove_only_keeps_local_and_cloud_archive_evidence(self):
        reply = self.run_action('delete')
        self.assertIn('本地文件和云端影片均保留', reply['message'])
        self.uploader.process_actions(self.sources)
        self.assertEqual([t['hash'] for t in self.qb.items],['3'*40])
        self.assertTrue(all(Path(j['source']).exists() for j in self.jobs))
        self.assertEqual(len(self.native.files),2)
        self.assertEqual(archived_keys(self.state/'data/uploads.sqlite3',42),{(1,1)})
        self.assertFalse(any(g['id']=='series:42' for g in self.app.tasks()['items']))
        self.remote.assert_not_called()

    def test_delete_files_removes_only_selected_project_and_keeps_cloud_history(self):
        self.run_action('delete',True);self.uploader.process_actions(self.sources)
        self.assertFalse(any(Path(j['source']).exists() for j in self.jobs))
        self.assertFalse((self.media/'downloads/tv/1.mkv').exists())
        self.assertTrue((self.media/'downloads/movies/3.mkv').exists())
        self.assertEqual(len(self.native.files),0)
        self.assertEqual(archived_keys(self.state/'data/uploads.sqlite3',42),{(1,1)})

    def test_queued_upload_must_end_before_local_delete(self):
        self.uploader.update(self.jobs[1],state='queued',task_id='native',stage='/aliyun/.cinema-upload/test/2.mkv')
        self.remote.upload_task.return_value={'state':3}
        self.run_action('delete',True);self.uploader.process_actions(self.sources)
        self.assertTrue(Path(self.jobs[1]['source']).exists());self.assertEqual(len(self.qb.items),3)
        self.assertEqual(task_actions(self.state/'data/uploads.sqlite3')[0]['error'],'task_waiting_for_upload')
        self.remote.upload_task.return_value={'state':4};self.uploader.process_actions(self.sources)
        self.assertFalse(Path(self.jobs[1]['source']).exists())

    def test_shared_payload_and_active_imports_fail_closed(self):
        self.qb.items[2]['save_path']=self.qb.items[0]['save_path'];self.qb.files['3'*40][0]['name']='1.mkv'
        self.run_action('delete',True);self.uploader.process_actions(self.sources)
        self.assertEqual(task_actions(self.state/'data/uploads.sqlite3')[0]['error'],'cleanup_shared_file')
        self.assertTrue(Path(self.jobs[0]['source']).exists())

    def test_local_delete_protects_another_jobs_source(self):
        other = candidate('another', self.jobs[0]['source'], '/aliyun/TV/Another/1.mkv', 10)
        self.uploader.add(other)
        self.run_action('delete', True)
        self.uploader.process_actions(self.sources)
        self.assertTrue(Path(other['source']).exists())
        self.assertEqual(task_actions(self.state/'data/uploads.sqlite3')[0]['error'], 'cleanup_shared_file')

    def test_locally_deletes_verified_orphan_and_can_keep_unverified_remainder(self):
        self.qb.items = [t for t in self.qb.items if t['hash'] == '3'*40]
        self.run_action('delete', True)
        self.uploader.process_actions(self.sources)
        self.assertTrue(Path(self.jobs[0]['source']).exists())
        self.assertEqual(task_actions(self.state/'data/uploads.sqlite3')[0]['state'], 'blocked')
        self.app.task_action({}, {'id':'series:42','action':'keep-remaining'})
        self.assertEqual(task_actions(self.state/'data/uploads.sqlite3')[0]['state'], 'done')
        self.assertTrue(Path(self.jobs[1]['source']).exists())

    def test_hidden_unpublished_job_releases_destination_for_different_episodefile(self):
        self.run_action('delete', key='torrent:'+'2'*40)
        self.uploader.process_actions(self.sources)
        old = self.jobs[1]
        new = candidate('tv:314', old['source'], old['dest'], old['size'], added_on=99, tvdb_id=42)
        self.uploader.add(new)
        self.assertEqual(tuple(self.uploader.db.execute('SELECT id,state FROM jobs WHERE dest=?', (old['dest'],)).fetchone()),
                         (new['id'], 'pending'))

    def test_active_import_defers_file_deletion(self):
        self.native.busy=[{'name':'ManualImport','status':'started'}]
        self.run_action('delete',True);self.uploader.process_actions(self.sources)
        self.assertEqual(task_actions(self.state/'data/uploads.sqlite3')[0]['error'],'task_waiting_for_import')
        self.assertTrue(all(Path(j['source']).exists() for j in self.jobs))
        self.native.busy=[];self.uploader.process_actions(self.sources)
        self.assertFalse(any(Path(j['source']).exists() for j in self.jobs))

    def test_explicit_readded_torrent_is_not_permanently_hidden(self):
        self.run_action('delete',False,'torrent:'+'1'*40);self.uploader.process_actions(self.sources)
        fresh={**self.jobs[0],'added_on':99};self.uploader.add(fresh)
        current=dict(self.uploader.db.execute('SELECT * FROM jobs WHERE id=?',(fresh['id'],)).fetchone())
        self.assertEqual(current['hidden'],0);self.assertFalse(self.uploader.blocked(current))

    def test_unrecognized_targets_and_boolean_confusion_rejected(self):
        for body in ({'id':'series:999','action':'delete'}, {'id':'series:42','action':'delete','delete_files':'false'}, {'id':'../etc','action':'pause'}):
            with self.assertRaises(ValueError):self.app.task_action({},body)
        self.assertTrue(self.native.series[0]['monitored'])

    def test_one_resource_operation_does_not_touch_other_episodes(self):
        self.native.queue.insert(0, {'downloadId':None,'seriesId':9,'episodeId':999})
        self.run_action('delete',True,'torrent:'+'2'*40);self.uploader.process_actions(self.sources)
        self.assertTrue(Path(self.jobs[0]['source']).exists());self.assertFalse(Path(self.jobs[1]['source']).exists())
        self.assertTrue(self.native.series[0]['monitored'])

    def test_queue_reassignment_cannot_unmonitor_another_series(self):
        digest = self.qb.items[1]['hash']
        snapshots = iter([self.native.queue, [{'downloadId':digest,'seriesId':10,'episodeId':301}]])
        original = self.native.json
        def reassigned(method, path, body=None):
            if path.startswith('/api/v3/queue?'):
                batch = next(snapshots)
                return {'records':batch,'totalRecords':len(batch)}
            return original(method, path, body)
        self.native.json = reassigned
        with self.assertRaisesRegex(ValueError, '剧集已变化'):
            self.run_action('cancel', key='torrent:'+digest)
        self.assertFalse(any(c[0]=='PUT' for c in self.native.calls))
        self.assertFalse(task_actions(self.state/'data/uploads.sqlite3'))

    def test_resource_resume_restores_only_original_monitoring_after_download_resume(self):
        digest = self.qb.items[1]['hash']
        for episode, monitored in [(103, False), (104, True)]:
            self.native.queue.append({'downloadId':digest,'seriesId':9,'episodeId':episode})
            self.native.monitored_episodes[episode] = monitored
        self.run_action('cancel', key='torrent:'+digest)
        self.assertTrue(self.native.monitored_episodes[102])
        self.native.monitored_episodes[104] = False  # Changed in Sonarr while waiting for the worker.
        native_json = self.native.json
        def lost_response(method, path, body=None, **kwargs):
            value = native_json(method, path, body, **kwargs)
            if path.endswith('/episode/monitor'):
                raise OSError('response lost')
            return value
        with patch.object(self.native, 'json', side_effect=lost_response):
            self.uploader.process_actions(self.sources)
        self.uploader.process_actions(self.sources)
        self.assertEqual(self.native.monitored_episodes, {101:True,102:False,103:False,104:False})
        with patch.object(self.qb, 'request', side_effect=OSError('unavailable')), self.assertRaisesRegex(ValueError, '尚未确认'):
            self.run_action('resume', key='torrent:'+digest)
        self.assertFalse(self.native.monitored_episodes[102])
        def interrupt(method, path, body=None):
            if path.endswith('/episode/monitor'):
                raise KeyboardInterrupt
            return native_json(method, path, body)
        with patch.object(self.native, 'json', side_effect=interrupt), self.assertRaises(KeyboardInterrupt):
            self.run_action('resume', key='torrent:'+digest)
        self.assertEqual(self.qb.items[1]['state'], 'downloading')
        self.assertEqual(task_actions(self.app.data/'uploads.sqlite3')[0]['error'], 'task_control_failed')
        self.run_action('resume', key='torrent:'+digest)
        self.assertEqual(self.native.monitored_episodes, {101:True,102:True,103:False,104:False})

    def test_readded_and_legacy_resources_do_not_restore_unproven_episode_monitoring(self):
        digest = self.qb.items[1]['hash']
        key = 'torrent:'+digest
        self.run_action('cancel', key=key)
        self.uploader.process_actions(self.sources)
        action = task_actions(self.app.data/'uploads.sqlite3')[0]
        legacy = action['payload'].copy()
        legacy['episode_ids'] = legacy.pop('monitored_episode_ids')
        with self.uploader.db:
            self.uploader.db.execute('UPDATE task_actions SET payload=? WHERE id=?', (json.dumps(legacy), key))
        self.run_action('resume', key=key)
        self.assertFalse(self.native.monitored_episodes[102])
        with self.uploader.db:
            self.uploader.db.execute("UPDATE task_actions SET action='delete',state='done',payload=? WHERE id=?",
                                     (json.dumps(action['payload']), key))
        with self.assertRaisesRegex(ValueError, '等待取消或删除完成'):
            self.run_action('resume', key=key)
        self.qb.items[1]['added_on'] += 100
        self.run_action('resume', key=key)
        self.assertFalse(self.native.monitored_episodes[102])
        self.assertEqual(self.qb.items[1]['state'], 'downloading')

    def test_blocked_cancel_can_be_ended_after_the_torrent_disappears(self):
        torrent = self.qb.items[1]
        key = 'torrent:'+torrent['hash']
        self.run_action('cancel', key=key)
        torrent['added_on'] += 100
        self.uploader.process_actions(self.sources)
        self.qb.items.remove(torrent)
        show = next(g for g in self.app.tasks()['items'] if g['id']=='series:42')
        self.assertTrue(show['resource_actions'][key]['can_keep'])
        self.app.task_action({}, {'id':key,'action':'keep-remaining'})
        self.assertFalse(task_actions(self.app.data/'uploads.sqlite3'))
        self.assertTrue(Path(self.jobs[1]['source']).exists())

    def test_resuming_one_cancelled_resource_keeps_the_other_stopped(self):
        self.run_action('cancel');self.uploader.process_actions(self.sources)
        self.run_action('resume',key='torrent:'+'2'*40)
        jobs=[dict(r) for r in self.uploader.db.execute('SELECT * FROM jobs ORDER BY rowid')]
        self.assertFalse(self.uploader.blocked(jobs[1]));self.assertEqual(jobs[1]['state'],'pending')
        self.assertEqual(self.qb.items[0]['state'],'stoppedUP');self.assertEqual(self.qb.items[1]['state'],'downloading')
        self.assertFalse(self.native.series[0]['monitored'])

    def test_previously_removed_entry_keeps_its_files_when_remaining_project_is_deleted(self):
        self.run_action('delete',False,'torrent:'+'1'*40);self.uploader.process_actions(self.sources)
        self.run_action('delete',True);self.uploader.process_actions(self.sources)
        self.assertTrue(Path(self.jobs[0]['source']).exists());self.assertFalse(Path(self.jobs[1]['source']).exists())
        project=next(a for a in task_actions(self.state/'data/uploads.sqlite3') if a['id']=='series:42')
        self.assertEqual(project['state'],'done');self.assertIsNone(project['error'])

    def test_watch_session_contains_only_this_user_and_server_identity(self):
        self.native.json=Mock(return_value={'Id':'server','ServerName':'Cinema'})
        result=self.app.watch_session({'user':'admin','token':'user-session'})
        self.assertEqual(result,{'server_id':'server','name':'Cinema','user_id':'admin','token':'user-session'})


class UploadCancellationTests(unittest.TestCase):
    setUp=test_uploader.UploaderTests.setUp
    tearDown=test_uploader.UploaderTests.tearDown
    row=test_uploader.UploaderTests.row
    run_upload=test_uploader.UploaderTests.run_upload

    def test_cancellation_arriving_during_put_cannot_publish(self):
        original=self.remote.put
        def put(*args):
            result=original(*args)
            payload={'jobs':[self.item['id']],'torrents':[]}
            with self.uploader.db:self.uploader.db.execute("INSERT INTO task_actions VALUES ('job','cancel',?,'pending',NULL,0)",(json.dumps(payload),))
            return result
        self.remote.put=put;self.run_upload()
        self.assertEqual(self.row()['state'],'uploaded');self.assertEqual(self.service.moves,0)
        self.assertEqual(self.row()['error'],'task_cancelling')


if __name__=='__main__':unittest.main()
