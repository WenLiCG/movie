"""Run: python -m unittest discover -s tests -p test_emby_defaults.py"""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from integration import emby_defaults
from portal.server import App


class Emby:
    def __init__(self):
        self.calls = []
        self.libraries = []
        self.options = dict(PreferredMetadataLanguage='en', MetadataCountryCode='US',
                            EnableRealtimeMonitor=False, SaveLocalMetadata=True, MetadataSavers=['Nfo'],
                            SaveLocalThumbnailSets=True, DownloadImagesInAdvance=False,
                            EnableChapterImageExtraction=True, ExtractChapterImagesDuringLibraryScan=True,
                            EnableMarkerDetection=True, EnableMarkerDetectionDuringLibraryScan=True,
                            SubtitleDownloadLanguages=[], SubtitleDownloadMaxAgeDays=180,
                            RequirePerfectSubtitleMatch=True, SaveSubtitlesWithMedia=True,
                            SkipSubtitlesIfEmbeddedSubtitlesPresent=False, SkipSubtitlesIfAudioTrackMatches=False,
                            SubtitleFetcherOrder=[], DisabledSubtitleFetchers=[],
                            TypeOptions=[], CustomOption='keep')
        self.types = [{'Type': 'Movie', 'MetadataFetchers': [{'Name': 'TheTVDB', 'DefaultEnabled': True},
                      {'Name': 'TheMovieDb', 'DefaultEnabled': False}],
                      'ImageFetchers': [{'Name': 'Image Capture', 'DefaultEnabled': True},
                      {'Name': 'TheMovieDb', 'DefaultEnabled': True}],
                      'DefaultImageOptions': [{'Type': 'Primary', 'Limit': 1, 'MinWidth': 0}]}]

    def json(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path == '/Library/VirtualFolders/Query':
            return {'Items': copy.deepcopy(self.libraries)}
        return {'DefaultLibraryOptions': copy.deepcopy(self.options), 'TypeOptions': copy.deepcopy(self.types),
                'SubtitleFetchers': [{'Name': 'Open Subtitles', 'DefaultEnabled': True}]}

    def request(self, method, path, body=None):
        self.calls.append((method, path, copy.deepcopy(body)))
        if path == '/Library/VirtualFolders':
            self.libraries.append({'Name': body['Name'], 'CollectionType': body['CollectionType'],
                                   'Locations': body['Paths'], 'LibraryOptions': copy.deepcopy(body['LibraryOptions']),
                                   'ItemId': str(len(self.libraries)+1)})
        elif path == '/Library/VirtualFolders/LibraryOptions':
            next(lib for lib in self.libraries if lib['ItemId'] == body['Id'])['LibraryOptions'] = copy.deepcopy(body['LibraryOptions'])


class DefaultsTests(unittest.TestCase):
    def test_first_login_initializes_once_across_restart_and_manual_restore_still_works(self):
        with tempfile.TemporaryDirectory() as temp, patch('portal.server.emby_defaults') as apply:
            native = Mock()
            native.json.return_value = {'AccessToken': 'private-token', 'User': {'Id': '1', 'Policy': {'IsAdministrator': True}}}
            app = App(Path(temp)); app.client = lambda _: native
            app.login({'username': 'admin', 'password': 'private-password'})
            app.login({'username': 'admin', 'password': 'private-password'})
            restarted = App(Path(temp)); restarted.client = lambda _: native
            restarted.login({'username': 'admin', 'password': 'private-password'})
            apply.assert_called_once_with(native)
            self.assertEqual((Path(temp) / 'emby-defaults.applied').read_text(), '1\n')
            restarted.metadata_defaults({'token': 'private-token'})
            self.assertEqual(apply.call_count, 2)

    def test_initialization_failure_keeps_login_available_and_retries(self):
        with tempfile.TemporaryDirectory() as temp, patch('portal.server.emby_defaults', side_effect=[ValueError('unavailable'), None]) as apply:
            app = App(Path(temp)); native = Mock(); app.client = lambda _: native
            native.json.return_value = {'AccessToken': 'token', 'User': {'Id': '1', 'Policy': {'IsAdministrator': True}}}
            sid, _ = app.login({'username': 'admin', 'password': 'password'})
            self.assertIn(sid, app.sessions)
            self.assertFalse((Path(temp) / 'emby-defaults.applied').exists())
            app.login({'username': 'admin', 'password': 'password'})
            self.assertTrue((Path(temp) / 'emby-defaults.applied').is_file())
            self.assertEqual(apply.call_count, 2)

    def test_non_administrator_cannot_initialize_defaults(self):
        with tempfile.TemporaryDirectory() as temp, patch('portal.server.emby_defaults') as apply:
            app = App(Path(temp)); native = Mock(); app.client = lambda _: native
            native.json.return_value = {'AccessToken': 'token', 'User': {'Id': '1', 'Policy': {'IsAdministrator': False}}}
            with self.assertRaises(ValueError): app.login({'username': 'viewer', 'password': 'password'})
            apply.assert_not_called()
            self.assertFalse((Path(temp) / 'emby-defaults.applied').exists())

    def test_creates_native_libraries_and_only_fills_missing_metadata(self):
        api = Emby()
        emby_defaults(api)
        self.assertEqual([lib['Name'] for lib in api.libraries], ['电影', '电视剧'])
        self.assertEqual([lib['Locations'] for lib in api.libraries], [['/library/aliyun/Movies'], ['/library/aliyun/TV']])
        for lib in api.libraries:
            options = lib['LibraryOptions']
            self.assertEqual(options['PreferredMetadataLanguage'], 'zh')
            self.assertEqual(options['MetadataCountryCode'], 'CN')
            self.assertEqual(options['TypeOptions'][0]['MetadataFetchers'], ['TheMovieDb', 'TheTVDB'])
            self.assertEqual(options['TypeOptions'][0]['ImageFetchers'], ['TheMovieDb'])
            self.assertFalse(options['SaveLocalMetadata'])
            self.assertFalse(options['EnableMarkerDetection'])
            self.assertFalse(options['EnableChapterImageExtraction'])
            self.assertEqual(options['SubtitleDownloadLanguages'], ['zh-CN', 'zh-TW'])
            self.assertEqual(options['SubtitleFetcherOrder'], ['Open Subtitles'])
            self.assertEqual(options['SubtitleDownloadMaxAgeDays'], 0)
            self.assertFalse(options['RequirePerfectSubtitleMatch'])
            self.assertFalse(options['SaveSubtitlesWithMedia'])
        refreshes = [path for method, path, body in api.calls if path.startswith('/Items/')]
        self.assertEqual(len(refreshes), 2)
        self.assertTrue(all('ReplaceAllMetadata=false&ReplaceAllImages=false' in path for path in refreshes))
        # Native item titles, provider IDs, watch history and posters are never written.
        self.assertTrue(all(path.startswith(('/Library/', '/Libraries/', '/Items/')) for _, path, _ in api.calls))

    def test_reapplying_preserves_names_paths_and_unrelated_options(self):
        api = Emby()
        emby_defaults(api)
        api.libraries[0]['Name'] = '我的电影'
        api.libraries[0]['LibraryOptions']['CustomOption'] = 'manual'
        before = copy.deepcopy(api.libraries)
        emby_defaults(api)
        self.assertEqual(api.libraries, before)
        self.assertEqual(sum(path == '/Library/VirtualFolders' for _, path, _ in api.calls), 2)

    def test_existing_manual_subtitle_preferences_are_preserved(self):
        api = Emby()
        api.options.update(SubtitleDownloadLanguages=['zh'], RequirePerfectSubtitleMatch=True,
                           SubtitleDownloadMaxAgeDays=30, SaveSubtitlesWithMedia=True,
                           SubtitleFetcherOrder=['Manual'], DisabledSubtitleFetchers=['Open Subtitles'])
        emby_defaults(api)
        for library in api.libraries:
            for key, value in api.options.items():
                if 'Subtitle' in key:
                    self.assertEqual(library['LibraryOptions'][key], value)

    def test_server_must_confirm_subtitle_preferences(self):
        api = Emby()
        original = api.request
        def ignoring_language(method, path, body=None):
            original(method, path, body)
            for library in api.libraries:
                library['LibraryOptions']['SubtitleDownloadLanguages'] = []
        api.request = ignoring_language
        with self.assertRaises(ValueError): emby_defaults(api)

    def test_conflicts_and_missing_provider_fail_before_any_write(self):
        for mode in ('type', 'shared', 'duplicate', 'name', 'provider'):
            with self.subTest(mode=mode):
                api = Emby()
                if mode == 'provider':
                    api.types[0]['MetadataFetchers'] = []
                else:
                    lib = {'Name': '电视剧', 'CollectionType': 'tvshows', 'Locations': ['/library/aliyun/TV']}
                    if mode == 'type': lib['CollectionType'] = 'movies'
                    if mode == 'shared': lib['Locations'].append('/unrelated')
                    if mode == 'name': lib['Locations'] = ['/unrelated']
                    api.libraries = [lib, copy.deepcopy(lib)] if mode == 'duplicate' else [lib]
                with self.assertRaises(ValueError): emby_defaults(api)
                self.assertFalse(any(method == 'POST' for method, _, _ in api.calls))
