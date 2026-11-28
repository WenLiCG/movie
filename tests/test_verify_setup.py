from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from portal.verify_setup import UPLOADER_CONNECTIONS, verify


class VerifySetupTests(unittest.TestCase):
    def test_requires_two_fresh_heartbeats_and_all_portal_checks(self):
        for case in ('first startup', 'stalled', 'disk check failed', 'missing config', 'unconfigured indexer'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temp:
                app = Mock(data=Path(temp))
                app.config.return_value = {} if case == 'missing config' else dict.fromkeys(UPLOADER_CONNECTIONS, 'saved')
                checks = {key: {'complete': True, 'message': '正常'} for key in
                          ('cloud', 'downloader', 'indexers', 'television', 'retention', 'metadata', 'uploader')}
                if case == 'unconfigured indexer':
                    checks['indexers'] = {'complete': False, 'message': '尚未启用站点'}
                if case == 'missing config':
                    checks['uploader'] = {'complete': False, 'message': '等待接线'}
                if case == 'disk check failed':
                    checks['uploader'] = {'complete': False, 'message': '空间检查暂不可用'}
                app.status.return_value = {'checks': checks}
                now = time.time()
                elapsed = 0
                def advance(seconds):
                    nonlocal elapsed
                    elapsed += seconds
                    if elapsed >= 30:
                        at = now - 2 if elapsed < 40 or case in ('stalled', 'disk check failed') else now - 1
                        (app.data / 'heartbeat.json').write_text(json.dumps({'at': at}))
                output = StringIO()
                with patch('portal.verify_setup.time.monotonic', side_effect=lambda: elapsed), \
                     patch('portal.verify_setup.time.sleep', side_effect=advance), redirect_stdout(output):
                    self.assertEqual(verify(app), case == 'first startup')
                app.status.assert_called_once()
                if case == 'missing config':
                    self.assertEqual(elapsed, 0)
                    self.assertIn('等待接线', output.getvalue())
                if case == 'stalled':
                    self.assertIn('未观察到两次递增的有效心跳', output.getvalue())
                if case == 'disk check failed':
                    self.assertIn('空间检查暂不可用', output.getvalue())
                    self.assertNotIn('未观察到两次递增的有效心跳', output.getvalue())
