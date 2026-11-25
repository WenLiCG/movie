"""Run: python -m unittest discover -s tests -p test_delete_all.py"""
import copy
import hashlib
import json
from pathlib import Path, PurePosixPath
import unittest
from unittest.mock import Mock
from archive import task_actions
import test_tasks
from uploader import OpenList, Failure, candidate


class Cloud(OpenList):
    def __init__(self):
        self.objects = {p:{'is_dir':True,'size':0} for p in ['/aliyun','/aliyun/Movies','/aliyun/Movies/Old','/aliyun/Movies/New']}
        self.calls, self.outage, self.ignore_remove = [], False, False

    def entries(self, directory):
        if self.outage or directory not in self.objects:
            raise Failure('openlist_code_500')
        return [{**copy.deepcopy(v),'name':PurePosixPath(p).name} for p,v in self.objects.items() if str(PurePosixPath(p).parent)==directory]

    def call(self, action, value):
        self.calls.append((action,copy.deepcopy(value)))
        if action=='get': return copy.deepcopy(self.objects[value['path']])
        if action=='remove':
            if not self.ignore_remove:
                for name in value['names']:
                    path=value['dir']+'/'+name
                    if self.objects.get(path,{}).get('is_dir'): assert not self.entries(path)
                    self.objects.pop(path, None)
        else: raise AssertionError(action)


class Emby:
    def __init__(self, root):
        self.root, self.ready, self.requests = root, True, []
        self.old = []

    def json(self, method, path, query=None):
        if path=='/Library/VirtualFolders/Query':
            return {'Items':[{'ItemId':name.lower(),'Locations':['/library/aliyun/'+name]} for name in ['Movies','TV']]}
        assert path=='/Items'
        paths = ['/library/'+p.relative_to(self.root).as_posix() for p in self.root.rglob('*.mkv')] if self.ready else self.old
        return {'Items':[{'Path':p,'MediaSources':[{'Path':p}]} for p in paths],'TotalRecordCount':len(paths)}

    def request(self, method, path):
        self.requests.append((method,path))


class DeleteAllTests(unittest.TestCase):
    def setUp(self):
        test_tasks.TaskTests.setUp(self)
        self.cloud=Cloud();self.uploader.remote=self.cloud
        self.index=self.root/'index';self.index.mkdir();self.uploader.library=self.index
        self.sources.emby=Emby(self.index)
        self.app.ensure_emby_maintenance=Mock()
        self.app.localize=Mock(return_value={})
        self.selected=[]
        for filename,data in [('3.mkv',b'frame-data'),('3.zh.srt','中文字幕'.encode())]:
            source=self.media/'downloads/movies'/filename;source.write_bytes(data)
            if filename.endswith('.srt'):self.qb.files['3'*40].append({'index':1,'name':filename,'size':len(data),'priority':1,'progress':1})
            item=candidate('movie:'+filename,source,'/aliyun/Movies/Old/'+filename,len(data),torrent_hash='3'*40,added_on=3)
            if self.selected:item['parent_id']=self.selected[0]['id']
            self.uploader.add(item);self.uploader.update(item,state='published',sha1=hashlib.sha1(data).hexdigest())
            self.selected.append(item)
            self.cloud.objects[item['dest']]={'is_dir':False,'size':len(data),'hash_info':{'sha1':item['sha1']}}
            path=self.index/item['dest'].lstrip('/');path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'virtual-index')
        self.new='/aliyun/Movies/New/new.mkv';self.cloud.objects[self.new]={'is_dir':False,'size':10,'hash_info':{'sha1':'f'*40}}
        path=self.index/self.new.lstrip('/');path.parent.mkdir(parents=True);path.write_bytes(b'new-index')
        self.sources.emby.old=['/library'+j['dest'] for j in self.selected]

    tearDown=test_tasks.TaskTests.tearDown

    def submit(self, key='torrent:'+'3'*40):
        session = {'token':'admin'}
        body = {'id':key,'action':'delete','delete_files':True,'delete_all':True}
        scope = self.app.task_action(session, {**body, 'preview':True})['scope']
        return self.app.task_action(session, {**body, 'scope':scope})

    def action_state(self):
        return next(a for a in task_actions(self.state/'data/uploads.sqlite3') if a['id']=='torrent:'+'3'*40)

    def test_removes_exact_version_subtitles_local_files_and_index_after_scan(self):
        self.submit();self.uploader.process_actions(self.sources)
        self.assertEqual(self.action_state()['state'],'done')
        self.assertEqual(len(self.qb.items),2)
        for job in self.selected:
            self.assertNotIn(job['dest'],self.cloud.objects)
            self.assertFalse(Path(job['source']).exists())
            self.assertFalse((self.index/job['dest'].lstrip('/')).exists())
            self.assertIsNone(self.uploader.db.execute('SELECT id FROM jobs WHERE id=?',(job['id'],)).fetchone())
        self.assertIn(self.new,self.cloud.objects)
        self.assertNotIn('/aliyun/Movies/Old',self.cloud.objects)
        self.assertEqual((self.index/self.new.lstrip('/')).read_bytes(),b'new-index')
        self.assertEqual(len(list((self.state/'data/deleted-indexes').rglob('*.*'))),2)
        self.assertTrue(self.sources.emby.requests)
        fresh={**self.selected[0],'added_on':99}
        Path(fresh['source']).write_bytes(b'frame-data');self.uploader.add(fresh)
        current=dict(self.uploader.db.execute('SELECT * FROM jobs WHERE id=?',(fresh['id'],)).fetchone())
        self.assertEqual(current['state'],'pending');self.assertFalse(self.uploader.blocked(current))
        other=candidate('new-release',fresh['source'],self.selected[1]['dest'],10,torrent_hash='4'*40,added_on=99)
        self.uploader.add(other)  # Deleted destination reservations also permit a different release identity.

    def test_changed_cloud_object_fails_before_any_cloud_delete(self):
        self.cloud.objects[self.selected[1]['dest']]['hash_info']['sha1']='0'*40
        self.submit();self.uploader.process_actions(self.sources)
        self.assertEqual(self.action_state()['error'],'remote_hash_mismatch')
        self.assertTrue(all(j['dest'] in self.cloud.objects for j in self.selected))
        self.assertTrue(Path(self.selected[0]['source']).exists())

    def test_outage_and_uncertain_remove_are_not_success(self):
        self.submit();self.cloud.outage=True;self.uploader.process_actions(self.sources)
        self.assertEqual(self.action_state()['state'],'pending')
        self.assertTrue((self.index/self.selected[0]['dest'].lstrip('/')).exists())
        self.cloud.outage=False;self.cloud.ignore_remove=True;self.uploader.process_actions(self.sources)
        self.assertEqual(self.action_state()['error'],'delete_all_waiting_cloud')
        self.assertTrue(Path(self.selected[0]['source']).exists())

    def test_missing_cloud_parent_is_proven_from_healthy_ancestor(self):
        for job in self.selected:self.cloud.objects.pop(job['dest'])
        self.cloud.objects.pop('/aliyun/Movies/Old')
        self.submit();self.uploader.process_actions(self.sources)
        self.assertEqual(self.action_state()['state'],'done')
        self.assertFalse(any(a=='remove' for a,_ in self.cloud.calls))

    def test_pending_scan_survives_restart_and_cannot_be_replaced(self):
        self.sources.emby.ready=False
        self.submit();self.uploader.process_actions(self.sources)
        self.assertEqual(self.action_state()['error'],'delete_all_waiting_scan')
        self.assertTrue(Path(self.selected[0]['source']).exists())
        with self.assertRaisesRegex(ValueError,'等待'):self.submit()
        self.uploader.db.close()
        from uploader import Uploader
        self.uploader=Uploader(self.cloud,self.state/'data',self.media);self.uploader.library=self.index
        self.sources.emby.ready=True;self.uploader.process_actions(self.sources)
        self.assertEqual(self.action_state()['state'],'done')
        self.assertEqual(len(self.sources.emby.requests),1)

    def test_orphan_payload_requires_matching_hash(self):
        self.qb.items=[t for t in self.qb.items if t['hash']!='3'*40]
        source=Path(self.selected[0]['source']);source.write_bytes(b'new-data!!')
        self.submit();self.uploader.process_actions(self.sources)
        self.assertEqual(self.action_state()['error'],'delete_all_local_changed')
        self.assertTrue(source.exists())

    def test_changed_local_file_with_native_torrent_prevents_all_deletion(self):
        self.submit()
        source = Path(self.selected[0]['source'])
        source.write_bytes(b'new-data!!')
        self.uploader.process_actions(self.sources)
        self.assertEqual(self.action_state()['state'], 'blocked')
        self.assertEqual(source.read_bytes(), b'new-data!!')
        self.assertTrue(all(j['dest'] in self.cloud.objects for j in self.selected))

    def test_scope_conflict_and_job_arriving_after_submit(self):
        body = {'id':'series:42','action':'delete','delete_files':True,'delete_all':True}
        scope = self.app.task_action({}, {**body, 'preview':True})['scope']
        first = self.jobs[0]
        self.cloud.objects.update({'/aliyun/TV':{'is_dir':True}, '/aliyun/TV/Show':{'is_dir':True}})
        self.cloud.objects[first['dest']] = {'is_dir':False,'size':10,'hash_info':{'sha1':first['sha1']}}
        def add_episode(n):
            source = self.media/'library/tv/Show'/f'{n}.mkv'
            source.write_bytes(b'frame-data')
            job = candidate(f'tv:{n}', source, f'/aliyun/TV/Show/{n}.mkv', 10, tvdb_id=42)
            self.uploader.add(job)
            self.uploader.update(job, state='published', sha1=first['sha1'])
            self.cloud.objects[job['dest']] = copy.deepcopy(self.cloud.objects[first['dest']])
            return job
        earlier = add_episode(18)
        with self.assertRaisesRegex(ValueError, '清单已变化'):
            self.app.task_action({}, {**body, 'scope':scope})
        self.assertTrue(self.native.series[0]['monitored'])
        self.assertFalse(self.cloud.calls)
        self.submit('series:42')
        later = add_episode(19)
        self.uploader.process_actions(self.sources)
        self.assertNotIn(earlier['dest'], self.cloud.objects)
        self.assertIn(later['dest'], self.cloud.objects)
        self.assertTrue(Path(later['source']).exists())

    def test_legacy_pending_delete_requires_confirmation(self):
        self.submit()
        action = self.action_state()
        payload = action['payload']
        del payload['delete_targets']
        with self.uploader.db:
            self.uploader.db.execute('UPDATE task_actions SET payload=? WHERE id=?', (json.dumps(payload), action['id']))
        self.uploader.process_actions(self.sources)
        self.assertEqual(self.action_state()['error'], 'delete_scope_required')
        self.assertTrue(all(j['dest'] in self.cloud.objects for j in self.selected))

    def test_preview_includes_group_files_older_than_display_limit(self):
        with self.uploader.db:
            self.uploader.db.executemany('INSERT INTO jobs (id,source,dest,size) VALUES (?,?,?,?)',
                ((f'history-{i}', '/unused', f'/aliyun/Movies/History/{i}.mkv', 1) for i in range(1001)))
        self.assertNotIn(self.jobs[0]['id'], {j['id'] for j in self.app.uploads()})
        preview = self.app.task_action({}, {'id':'series:42','action':'delete',
                                             'delete_files':True,'delete_all':True,'preview':True})
        self.assertEqual(preview['cloud'], [self.jobs[0]['dest']])
        self.assertIn(self.jobs[0]['source'], preview['local'])

    def test_verified_orphan_payload_is_removed_without_a_torrent(self):
        self.qb.items=[t for t in self.qb.items if t['hash']!='3'*40]
        self.submit();self.uploader.process_actions(self.sources)
        self.assertEqual(self.action_state()['state'],'done')
        self.assertFalse(any(Path(j['source']).exists() for j in self.selected))

    def test_orphan_shared_with_another_torrent_preserves_all_data(self):
        self.qb.items=[t for t in self.qb.items if t['hash']!='3'*40]
        self.qb.items[0]['save_path']=str(self.media/'downloads/movies');self.qb.files['1'*40][0]['name']='3.mkv'
        self.submit();self.uploader.process_actions(self.sources)
        self.assertEqual(self.action_state()['error'],'cleanup_shared_file')
        self.assertTrue(all(j['dest'] in self.cloud.objects for j in self.selected))

    def test_source_claimed_by_another_archive_job_is_preserved(self):
        other=candidate('other',self.selected[0]['source'],'/aliyun/Movies/Other/file.mkv',10,torrent_hash='4'*40,added_on=4)
        self.uploader.add(other)
        self.submit();self.uploader.process_actions(self.sources)
        self.assertEqual(self.action_state()['error'],'cleanup_shared_file')
        self.assertTrue(all(j['dest'] in self.cloud.objects for j in self.selected))

    def test_directory_reserved_for_another_upload_is_not_removed(self):
        source=self.media/'downloads/movies/other.mkv';source.write_bytes(b'frame-data')
        other=candidate('other',source,'/aliyun/Movies/Old/incoming.mkv',10,torrent_hash='4'*40,added_on=4)
        self.uploader.add(other)
        self.submit();self.uploader.process_actions(self.sources)
        self.assertEqual(self.action_state()['state'],'done')
        self.assertIn('/aliyun/Movies/Old',self.cloud.objects)
        self.assertIsNotNone(self.uploader.db.execute('SELECT id FROM jobs WHERE id=?',(other['id'],)).fetchone())

    def test_tv_delete_preserves_imported_sidecar_added_after_confirmation(self):
        self.submit('series:42')
        video = Path(self.jobs[0]['source'])
        sidecar = video.with_suffix('.zh.srt')
        sidecar.write_bytes(b'only local copy')
        self.uploader.process_actions(self.sources)
        action = next(a for a in task_actions(self.state/'data/uploads.sqlite3') if a['id']=='series:42')
        self.assertEqual((action['state'], action['error']), ('blocked', 'cleanup_local_unverified'))
        self.assertTrue(video.exists())
        self.assertTrue(sidecar.exists())
        self.assertFalse(any(call[0] == 'DELETE' for call in self.native.calls))

    def test_tv_project_clears_archive_evidence_but_preserves_other_versions(self):
        self.cloud.objects.update({'/aliyun/TV':{'is_dir':True},'/aliyun/TV/Show':{'is_dir':True}})
        job=self.jobs[0]
        self.cloud.objects[job['dest']]={'is_dir':False,'size':job['size'],'hash_info':{'sha1':job['sha1']}}
        path=self.index/job['dest'].lstrip('/');path.parent.mkdir(parents=True);path.write_bytes(b'virtual-index')
        self.submit('series:42')
        self.uploader.process_actions(self.sources)
        from archive import archived_keys
        action=next(a for a in task_actions(self.state/'data/uploads.sqlite3') if a['id']=='series:42')
        self.assertEqual(action['state'],'done')
        self.assertEqual(archived_keys(self.state/'data/uploads.sqlite3',42),set())
        self.assertEqual(len(self.native.files),0)
        self.assertIn(self.selected[0]['dest'],self.cloud.objects)

    def test_repeated_delete_after_redownload_keeps_distinct_index_backups(self):
        self.submit();self.uploader.process_actions(self.sources)
        fresh={**self.selected[0],'added_on':99}
        Path(fresh['source']).write_bytes(b'frame-data');self.uploader.add(fresh)
        self.uploader.update(fresh,state='published',sha1=hashlib.sha1(b'frame-data').hexdigest())
        self.qb.items.append({'hash':'3'*40,'name':'Movie','category':'cinema-movies',
                              'save_path':str(self.media/'downloads/movies'),'added_on':99,'state':'uploading','progress':1,'size':10})
        self.qb.files['3'*40] = self.qb.files['3'*40][:1]
        self.cloud.objects['/aliyun/Movies/Old']={'is_dir':True}
        self.cloud.objects[fresh['dest']]={'is_dir':False,'size':10,'hash_info':{'sha1':fresh['sha1']}}
        path=self.index/fresh['dest'].lstrip('/');path.parent.mkdir(parents=True);path.write_bytes(b'updated duration index')
        self.submit();self.uploader.process_actions(self.sources)
        self.assertEqual(self.action_state()['state'],'done')
        backups=list((self.state/'data/deleted-indexes').rglob('3.mkv'))
        self.assertEqual({p.read_bytes() for p in backups},{b'virtual-index',b'updated duration index'})

    def test_index_symlink_is_rejected_before_cloud_deletion(self):
        path=self.index/self.selected[0]['dest'].lstrip('/');path.unlink()
        protected=self.root/'protected';protected.write_bytes(b'keep')
        try: path.symlink_to(protected)
        except OSError: self.skipTest('Run the symlink boundary check on Linux')
        self.submit();self.uploader.process_actions(self.sources)
        self.assertEqual(self.action_state()['error'],'unsafe_or_missing_source')
        self.assertEqual(protected.read_bytes(),b'keep')
        self.assertIn(self.selected[0]['dest'],self.cloud.objects)

    def test_unsafe_index_and_invalid_flags_are_rejected(self):
        for body in [{'delete_all':'true','delete_files':True},{'delete_all':True,'delete_files':False},{'delete_all':True,'delete_files':True,'action':'pause'}]:
            with self.assertRaises(ValueError):self.app.task_action({}, {'id':'torrent:'+'3'*40,'action':'delete',**body})
        path=self.index/self.selected[0]['dest'].lstrip('/');path.unlink();path.mkdir()
        self.submit();self.uploader.process_actions(self.sources)
        self.assertEqual(self.action_state()['error'],'delete_all_unsafe_index')
        self.assertIn(self.selected[0]['dest'],self.cloud.objects)


class MaintenanceKeyTests(unittest.TestCase):
    def test_creates_dedicated_key_and_keeps_existing_service_credentials(self):
        import tempfile
        from portal.server import App
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'config.json').write_text(json.dumps({'qb_password':'private-password'}))
            app=App(root);client=Mock();app.client=lambda _:client
            client.json.side_effect=[{'Items':[]},{'Items':[{'AppName':'Cinema library maintenance','AccessToken':'a'*32}]}]
            app.ensure_emby_maintenance({'token':'private-user-session'})
            value=json.loads((root/'config.json').read_text())
            self.assertEqual(value['qb_password'],'private-password')
            self.assertEqual(value['emby_token'],'a'*32)
            self.assertNotIn('private-user-session',json.dumps(value))
            client.request.assert_called_once()
