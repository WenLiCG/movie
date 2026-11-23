import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from portal.playback import playback_records
from portal.server import App


class PlaybackTests(unittest.TestCase):
    def test_api_enriches_titles_without_returning_credentials(self):
        app=App();native=Mock();app.client=lambda _:native
        native.json.return_value={'Items':[{'Id':'9','Name':'第一集','Type':'Episode','SeriesName':'示例剧','ParentIndexNumber':None,'IndexNumber':1}]}
        with patch('portal.server.playback_records',return_value=[{'item_id':'9'}]):
            result=app.playbacks({'token':'private-session-token'})
        self.assertEqual(result['items'][0]['title'],'示例剧 · S00E01 · 第一集')
        self.assertNotIn('private-session-token',json.dumps(result))
        self.assertNotIn('private-session-token',native.json.call_args.args[1])

    def read(self, events):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp)/'events.jsonl').write_text('\n'.join(json.dumps(e) for e in events)+'\n',encoding='utf-8')
            return playback_records(temp,now=1000)

    def event(self, kind='media', **fields):
        return {'at':900,'actor':'a'*24,'item':'9','kind':kind,'status':200,**fields}

    def test_redirect_then_real_progress_is_one_record_without_secrets(self):
        records=self.read([self.event(status=307,delivery='cloud',host='dl1-v6.aliyundrive.cloud'),
                           self.event('start',at=901,session='first'),
                           self.event('progress',at=915,session='first',position=150000000,client='Emby Web')])
        self.assertEqual(len(records),1)
        self.assertEqual(records[0]['route'],'cloud')
        self.assertTrue(records[0]['has_progress'])
        self.assertEqual(records[0]['position_seconds'],15)
        self.assertNotIn('actor',records[0]);self.assertNotIn('session',records[0])

    def test_playback_mode_and_playlist_are_not_direct_connection_proof(self):
        record=self.read([self.event(delivery='playlist'),self.event('progress',position=100000000)])[0]
        self.assertEqual(record['route'],'unknown')
        self.assertTrue(record['has_progress'])
        record=self.read([self.event(status=307,delivery='cloud',host='dl1-v6.aliyundrive.cloud')])[0]
        self.assertFalse(record['has_progress'])

    def test_vps_bytes_are_not_hidden_by_a_later_cloud_redirect(self):
        record=self.read([self.event(status=206,delivery='vps',bytes=1024),
                          self.event(at=905,status=307,delivery='cloud',host='dl1-v6.aliyundrive.cloud'),
                          self.event(at=910,status=403,delivery='failed')])[0]
        self.assertEqual(record['route'],'vps')
        self.assertEqual(record['state'],'failed')
        self.assertEqual(record['error_status'],403)

    def test_repeated_plays_and_devices_stay_separate(self):
        events=[self.event('start',session='first'),self.event('stop',at=905,session='first'),
                self.event('start',at=910,session='second'),self.event('start',at=915,actor='b'*24,session='third')]
        records=self.read(events)
        self.assertEqual(len(records),3)
        self.assertEqual(len({r['id'] for r in records}),3)
        self.assertEqual(records[-1]['state'],'stopped')

    def test_rotation_partial_lines_limit_and_malformed_records(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp)/'events.jsonl.1').write_text(json.dumps(self.event('start',session='first'))+'\n')
            events=[self.event('progress',at=915,session='first',position=50000000),
                    self.event(item='../secret'),self.event(status='bad'),self.event(session={}),{'at':900}]
            (Path(temp)/'events.jsonl').write_text('\n'.join(json.dumps(e) for e in events)+'\n{"partial":')
            records=playback_records(temp,now=1200)
            self.assertEqual(len(records),1)
            self.assertEqual(records[0]['position_seconds'],5)
            self.assertEqual(records[0]['state'],'inactive')
        self.assertEqual(len(self.read([self.event(actor=f'{i:024x}') for i in range(130)])),100)

    def test_rejected_session_reports_do_not_create_or_push_out_records(self):
        real=self.event('progress',at=950,session='real',position=100000000)
        forged=[self.event('start',at=960+i%30,actor=f'{i:024x}',status=401) for i in range(150)]
        records=self.read([real,*forged])
        self.assertEqual(len(records),1)
        self.assertTrue(records[0]['has_progress'])
