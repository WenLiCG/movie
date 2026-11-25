import copy
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from portal.server import App
from portal.versions import details, rule, matches


TITLE = 'Example.S01E01.1080p.NF.WEB-DL.DDP.5.1.H.264-HHWEB'


class Sonarr:
    def __init__(self):
        self.series = [{'id':9,'tvdbId':42,'title':'Example','qualityProfileId':1,'monitored':False,
                        'tags':[],'seasons':[{'seasonNumber':1},{'seasonNumber':2}]},
                       {'id':10,'tvdbId':99,'title':'Other','monitored':True,'tags':[]}]
        self.tags, self.profiles, self.qualities, self.calls = [], [], [], []
        self.releases = [{'title':TITLE,'guid':'private-guid','indexerId':7,'size':1000,
                          'approved':True,'downloadAllowed':True,'mappedSeriesId':9}]

    def json(self, method, path, body=None):
        self.calls.append((method,path,copy.deepcopy(body)))
        route=path.split('?')[0]
        if route == '/api/v3/parse':
            value={'series':self.series[0], 'parsedEpisodeInfo':{'seriesTitle':'Example','seasonNumber':1,'episodeNumbers':[1],'releaseGroup':'HHWEB'}}
        elif route == '/api/v3/series':
            if method == 'POST':self.series.insert(0,{**body,'id':9})
            value=self.series[0] if method == 'POST' else self.series
        elif route == '/api/v3/series/9':
            if method == 'PUT': self.series[0]=copy.deepcopy(body)
            value=self.series[0]
        elif route == '/api/v3/episode': value=[{'id':101,'seriesId':9,'seasonNumber':1,'episodeNumber':1,'hasFile':False}, {'id':201,'seriesId':9,'seasonNumber':2,'episodeNumber':1,'hasFile':False}]
        elif route == '/api/v3/qualityprofile/schema': value={'items':[{'quality':{'id':1,'name':'WEBDL-1080p'}},{'quality':{'id':2,'name':'WEBDL-2160p'}}]}
        elif route == '/api/v3/qualityprofile':
            if method == 'POST':self.qualities.append({**body,'id':1})
            value=self.qualities[-1] if method == 'POST' else self.qualities
        elif route == '/api/v3/tag':
            if method == 'POST':self.tags.append({**body,'id':11+len(self.tags)})
            value=self.tags[-1] if method == 'POST' else self.tags
        elif route == '/api/v3/releaseprofile':
            if method == 'POST':self.profiles.append({**body,'id':12})
            value=self.profiles[-1] if method == 'POST' else self.profiles
        elif route == '/api/v3/releaseprofile/12':
            if method == 'PUT':self.profiles[0]=copy.deepcopy(body)
            value=self.profiles[0]
        elif route == '/api/v3/queue':value={'records':[], 'totalRecords':0}
        elif route == '/api/v3/indexer':value=[{'id':7,'name':'Site (Prowlarr)'}]
        elif route == '/api/v3/release':value=self.releases if method == 'GET' else body
        elif route in {'/api/v3/episode/monitor','/api/v3/command'}:value={}
        else:raise AssertionError((method,path))
        return copy.deepcopy(value)


class VersionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.app=App(Path(self.temp.name))
        self.native=Sonarr()
        self.app.client=lambda service:self.native
        self.session={'results':{'r':(time.time()+600,{'title':TITLE,'guid':'private-guid','size':1000,'indexer':'Site','indexerId':1,'_kind':'tv','_query':'Example'})}}
        preview=self.app.preview_release(self.session,{'release_id':'r'})
        self.body={'release_id':'r','id':'42','confirmed':True,'fields':preview['fields'],'mode':'all'}
        self.native.calls.clear()

    def tearDown(self):self.temp.cleanup()

    def test_native_required_expression_is_conjunctive_and_accepts_packs(self):
        fields,pattern=rule(self.body['fields'])
        self.assertTrue(matches(pattern,TITLE))
        self.assertTrue(matches(pattern,TITLE.replace('S01E01','S01E01-E20').replace('H.264','x264')))
        for old,new in [('HHWEB','OTHER'),('H.264','H.265'),('1080p','2160p'),('NF','AMZN'),('DDP.5.1','AAC.2.0'),('WEB-DL','WEBRip')]:
            self.assertFalse(matches(pattern,TITLE.replace(old,new)),(old,new))
        fields['group']='Pure@HDSWEB'
        _,pattern=rule(fields)
        self.assertTrue(matches(pattern,TITLE.replace('HHWEB','Pure@HDSWEB')))
        self.assertFalse(matches(pattern,TITLE.replace('HHWEB','Pure@Other')))

    def test_unknown_fields_are_explicit_and_literal_inputs_are_not_regex(self):
        fields,_=details('Example.S01E01.1080p.WEB-DL.H264')
        self.assertEqual(fields['group'],'')
        with self.assertRaises(ValueError):rule(fields)
        with self.assertRaises(ValueError):rule({**self.body['fields'],'audio':'/anything/.*'})
        with self.assertRaises(ValueError):self.app.save_version(self.session,{**self.body,'confirmed':False})
        self.assertEqual(self.native.calls,[])

    def test_rules_are_saved_before_monitoring_and_missing_episode_search(self):
        self.app.follow(self.session,self.body)
        profile=self.native.profiles[0]
        self.assertEqual(profile['tags'],[11])
        self.assertEqual(profile['indexerId'],0)
        self.assertEqual(len(profile['required']),1)
        saved=next(i for i,c in enumerate(self.native.calls) if c[:2]==('POST','/api/v3/releaseprofile'))
        monitoring=next(i for i,c in enumerate(self.native.calls) if c[:2]==('PUT','/api/v3/series/9') and c[2]['monitored'])
        searching=next(i for i,c in enumerate(self.native.calls) if c[:2]==('POST','/api/v3/command'))
        self.assertLess(saved,monitoring)
        self.assertLess(monitoring,searching)
        self.assertEqual(self.native.series[1]['tags'],[])
        self.assertTrue(self.native.series[1]['monitored'])

    def test_restart_keeps_rules_and_selected_episodes_use_them(self):
        self.app.save_version(self.session,self.body)
        restarted=App(Path(self.temp.name));restarted.client=lambda service:self.native
        restarted.download_episodes({'series_id':9,'episode_ids':[101]})
        self.assertEqual(self.native.calls[-1],('POST','/api/v3/command',{'name':'EpisodeSearch','episodeIds':[101]}))
        self.native.profiles[0]['enabled']=False
        count=len(self.native.calls)
        with self.assertRaises(ValueError):restarted.download_episodes({'series_id':9,'episode_ids':[201]})
        self.assertFalse(any(c[0]=='POST' for c in self.native.calls[count:]))

    def test_new_series_has_rules_before_native_import_and_backfill(self):
        self.native.series=self.native.series[1:]
        self.app.follow(self.session,self.body)
        profile_at=next(i for i,c in enumerate(self.native.calls) if c[:2]==('POST','/api/v3/releaseprofile'))
        series_at=next(i for i,c in enumerate(self.native.calls) if c[:2]==('POST','/api/v3/series'))
        created=self.native.calls[series_at][2]
        self.assertLess(profile_at,series_at)
        self.assertEqual(created['tags'],[11])
        self.assertTrue(created['monitored'])
        self.assertTrue(created['addOptions']['searchForMissingEpisodes'])
        self.assertEqual(created['addOptions']['monitor'],'all')
        self.assertFalse(any(c[:2]==('POST','/api/v3/command') for c in self.native.calls))

    def test_existing_files_and_inflight_downloads_are_retained_when_switching(self):
        self.app.save_version(self.session,self.body)
        marker=Path(self.temp.name)/'existing.mkv';marker.write_bytes(b'existing')
        changed={**self.body,'fields':{**self.body['fields'],'subtitles':'CHS'}}
        self.session['results']['r'][1]['title']=TITLE+'.CHS'
        # A different subtitle marker still needs explicit replacement confirmation.
        with self.assertRaises(ValueError):self.app.save_version(self.session,changed)
        changed['fields']['group']='HHWEB'
        self.session['results']['r'][1]['title']=TITLE.replace('-HHWEB','.CHS-HHWEB')
        self.app.save_version(self.session,{**changed,'replace':True})
        self.assertEqual(marker.read_bytes(),b'existing')
        self.assertFalse(any(c[0]=='DELETE' for c in self.native.calls))

    def test_profile_save_failure_leaves_series_paused_without_search(self):
        self.native.series[0]['monitored']=True
        original=self.native.json
        def fail(method,path,body=None):
            if (method,path)==('POST','/api/v3/releaseprofile'):raise ValueError('upstream failure')
            return original(method,path,body)
        self.native.json=fail
        with self.assertRaises(ValueError):self.app.follow(self.session,self.body)
        self.assertFalse(self.native.series[0]['monitored'])
        self.assertFalse(any(c[:2]==('POST','/api/v3/command') for c in self.native.calls))

    def test_invalid_scope_is_rejected_before_pausing(self):
        self.native.series[0]['monitored']=True
        self.session['series']['42']['seasons'].append({'seasonNumber':0})
        for season in (0, True, 1.0, 999):
            with self.subTest(season=season), self.assertRaises(ValueError):
                self.app.follow(self.session,{**self.body,'mode':'season','season':season})
            self.assertTrue(self.native.series[0]['monitored'])
            self.assertFalse(any(c[0] in {'PUT','POST'} for c in self.native.calls))

    def test_future_and_selected_season_keep_the_same_version(self):
        self.app.follow(self.session,{**self.body,'mode':'future'})
        self.assertFalse(any(c[:2]==('POST','/api/v3/command') for c in self.native.calls))
        pattern=self.native.profiles[0]['required']
        self.native.calls.clear()
        self.app.follow(self.session,{**self.body,'mode':'season','season':2})
        self.assertEqual(self.native.profiles[0]['required'],pattern)
        self.assertIn(('PUT','/api/v3/episode/monitor',{'episodeIds':[201],'monitored':True}),self.native.calls)
        self.assertIn(('PUT','/api/v3/episode/monitor',{'episodeIds':[101],'monitored':False}),self.native.calls)

    def test_unrecognized_show_never_prepares_a_download(self):
        self.native.json=Mock(return_value={'parsedEpisodeInfo':None})
        result=self.app.preview_release(self.session,{'release_id':'r'})
        self.assertFalse(result['available'])
        self.assertTrue(all(c.args[0]=='GET' for c in self.native.json.call_args_list))

    def test_audio_count_and_subtitle_requirements_are_all_mandatory(self):
        title=TITLE.replace('-HHWEB','.6Audios.CHS.CHT-HHWEB')
        fields,_=details(title)
        _,pattern=rule(fields)
        self.assertTrue(matches(pattern,title))
        for marker in ('6Audios','CHS','CHT'):
            self.assertFalse(matches(pattern,title.replace(marker,'')))

    def test_chinese_subtitle_version_roundtrips_as_utf8_and_invalid_series_id_fails_cleanly(self):
        for bad_id in ('', None, True, 'not-a-number'):
            with self.assertRaisesRegex(ValueError, '请先预览资源并确认'):
                self.app.save_version(self.session, {**self.body, 'id': bad_id})
        self.session['results']['r'][1]['title'] = TITLE.replace('-HHWEB', '.中字.简繁-HHWEB')
        fields = {**self.body['fields'], 'subtitles': '中字,简繁'}
        self.app.save_version(self.session, {**self.body, 'fields': fields})
        reloaded = App(Path(self.temp.name))
        reloaded.client = lambda service: self.native
        self.assertEqual(reloaded.version_records()['42']['fields']['subtitles'], '中字,简繁')

    def test_one_off_download_uses_exact_cached_resource_without_touching_sonarr(self):
        prowlarr=Mock()
        prowlarr.json.return_value=[{'id':5,'name':'Cinema qB TV','implementation':'QBittorrent','enable':True,
                                    'fields':[{'name':k,'value':v} for k,v in {'host':'qbittorrent','port':8080,'category':'cinema-tv'}.items()]}]
        qb=Mock();qb.json.side_effect=lambda method,path: [] if path.endswith('/info') else {'cinema-tv':{'savePath':'/media/downloads/tv-manual'}}
        self.app.qb=lambda:qb
        self.app.client=lambda name:prowlarr if name=='prowlarr' else self.fail('One-off download accessed Sonarr')
        # Even unknown shows or another version of a followed show need no preview or saved rule.
        self.session.pop('version_previews')
        (self.app.state/'versions.json').write_text('{"42":{"existing":"unchanged"}}')
        with patch('portal.server.shutil.disk_usage',return_value=Mock(free=10**12,total=10**12)):
            result=self.app.download_release(self.session,{'release_id':'r'})
        self.assertIn('不创建或修改追剧',result['message'])
        self.assertEqual(prowlarr.json.call_args.args,('POST','/api/v1/search',{'guid':'private-guid','indexerId':1,'downloadClientId':5}))
        self.assertEqual((self.app.state/'versions.json').read_text(),'{"42":{"existing":"unchanged"}}')
        self.assertEqual(self.native.calls,[])
        # The row stays usable for 按此版本追更, but a second grab of the same release is refused.
        self.assertEqual(self.app.stored_release(self.session,'r','tv')['guid'],'private-guid')
        posts=len([c for c in prowlarr.json.call_args_list if c.args[0]=='POST'])
        with self.assertRaises(ValueError) as repeated:
            self.app.download_release(self.session,{'release_id':'r'})
        self.assertIn('已提交',str(repeated.exception))
        self.assertEqual(len([c for c in prowlarr.json.call_args_list if c.args[0]=='POST']),posts)

    def test_one_off_fails_closed_for_expired_results_wrong_client_or_disk(self):
        prowlarr=Mock();prowlarr.json.return_value=[]
        self.app.client=lambda name:prowlarr
        with patch('portal.server.shutil.disk_usage',return_value=Mock(free=10**12,total=10**12)),self.assertRaises(ValueError):
            self.app.download_release(self.session,{'release_id':'r'})
        self.assertFalse(any(c.args[0]=='POST' for c in prowlarr.json.call_args_list))
        self.assertIn('r',self.session['results'])
        with patch('portal.server.shutil.disk_usage',return_value=Mock(free=0,total=10**12)),self.assertRaises(ValueError):
            self.app.download_release(self.session,{'release_id':'r'})
        self.session['results']['r']=(0,self.session['results']['r'][1])
        with self.assertRaises(ValueError):self.app.download_release(self.session,{'release_id':'r'})

    def test_movie_download_explicitly_uses_movie_client(self):
        self.session['results']['r'][1]['_kind']='movie'
        prowlarr=Mock()
        client={'id':1,'name':'Cinema qB','implementation':'QBittorrent','enable':True,
                'fields':[{'name':k,'value':v} for k,v in {'host':'qbittorrent','port':8080,'category':'cinema-movies'}.items()]}
        prowlarr.json.return_value=[client]
        qb=Mock();qb.json.side_effect=lambda method,path: [] if path.endswith('/info') else {'cinema-movies':{'savePath':'/media/downloads/movies'}}
        self.app.qb=lambda:qb;self.app.client=lambda name:prowlarr
        with patch('portal.server.shutil.disk_usage',return_value=Mock(free=10**12,total=10**12)):
            client['categories']=[{'clientCategory':'cinema-tv'}]
            with self.assertRaises(ValueError):self.app.download_release(self.session,{'release_id':'r'},'movie')
            self.assertFalse(any(c.args[0]=='POST' for c in prowlarr.json.call_args_list))
            client['categories']=[]
            self.app.download_release(self.session,{'release_id':'r'},'movie')
        self.assertEqual(prowlarr.json.call_args.args[2]['downloadClientId'],1)

    def test_remove_tracking_keeps_metadata_rules_files_and_inflight_downloads(self):
        self.app.follow(self.session,self.body)
        saved=(self.app.state/'versions.json').read_bytes()
        self.native.calls.clear()
        result=self.app.remove_series({'id':9})
        self.assertIn('已有文件',result['message'])
        self.assertFalse(self.native.series[0]['monitored'])
        self.assertEqual(self.native.series[0]['monitorNewItems'],'none')
        tag=next(t for t in self.native.tags if t['label']=='cinema-unfollowed')
        self.assertIn(tag['id'],self.native.series[0]['tags'])
        self.assertEqual(len(self.native.series),2)
        self.assertFalse(any(c[0]=='DELETE' or c[1].startswith('/api/v3/command') for c in self.native.calls))
        self.assertEqual((self.app.state/'versions.json').read_bytes(),saved)
        # Native tag survives an App restart; an explicit follow restores visibility and monitoring.
        restarted=App(self.app.state);restarted.client=lambda name:self.native
        restarted.follow(self.session,self.body)
        self.assertNotIn(tag['id'],self.native.series[0]['tags'])
        self.assertTrue(self.native.series[0]['monitored'])

    def test_tv_search_returns_all_resources_without_movie_categories_or_private_urls(self):
        native=Mock()
        native.json.side_effect=lambda method,path,body=None: [] if path=='/api/v1/indexer' else [{'title':TITLE,'size':1000,'indexer':'Site','indexerId':1,'guid':'private-guid','downloadUrl':'https://private/passkey'}]
        self.app.client=lambda name:native
        result=self.app.search(self.session,'Example','tv')
        self.assertNotIn('categories=',native.json.call_args_list[0].args[1])
        self.assertEqual(result['items'][0]['group'],'HHWEB')
        self.assertEqual(result['items'][0]['episodes'],'S01E01')
        self.assertNotIn('private',json.dumps(result))
        with self.assertRaises(ValueError):self.app.stored_release(self.session,result['items'][0]['id'],'movie')


if __name__=='__main__':unittest.main()
