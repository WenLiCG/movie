import hashlib
import base64
import importlib.util
import io
import json
import os
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from scripts import update_files

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bundle', ROOT / 'scripts/build_all_in_one.py')
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)


def archive(name='install.sh', kind=tarfile.REGTYPE):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w:gz') as tar:
        entries = {path: tarfile.REGTYPE for path in ('install.sh', 'compose.yaml', 'configure.py', 'integration.py', 'relay/custom-js/search.js')}
        entries[name] = kind
        for path, entry_type in entries.items():
            entry = tarfile.TarInfo(path)
            entry.type = entry_type
            entry.linkname = '/tmp/escaped'
            data = b'#!/bin/bash\nexit 0\n'
            entry.size = len(data) if entry_type == tarfile.REGTYPE else 0
            tar.addfile(entry, io.BytesIO(data))
    return stream.getvalue()


class InstallerTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows SSH launcher')
    def test_windows_tunnel_prompts_and_rejects_command_or_option_injection(self):
        launcher = (ROOT / 'Open.cmd').read_text(encoding='utf-8')
        self.assertIn('set /p "CINEMA_SSH_TARGET=', launcher)
        self.assertIn('set "CINEMA_SSH_TARGET="', launcher)
        validation = next(line for line in launcher.splitlines() if line.startswith('powershell -NoProfile -Command '))
        code = validation.split('-Command "', 1)[1][:-1]
        for target, status in [('root@203.0.113.10', 0), ('admin@vps.example.com', 0),
                               ('', 1), ('-oProxyCommand=calc', 1), ('root@host" & calc', 1), ('root@host;calc', 1)]:
            result = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', code],
                                    env={**os.environ, 'CINEMA_SSH_TARGET': target}, capture_output=True)
            self.assertEqual(result.returncode, status, target)

    def test_portal_update_backs_up_and_preserves_state_and_media(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source, target = base / 'source', base / 'existing'
            source.mkdir(); target.mkdir()
            for name in bundle.FILES:
                (source / name).write_text('new source', encoding='utf-8')
            for name in bundle.DIRECTORIES:
                (source / name).mkdir()
            (target / '.env').write_text('private-settings')
            (target / 'compose.yaml').write_text('old source')
            (target / 'compose.override.yaml').write_text('machine-specific-network-settings')
            (target / 'state/relay').mkdir(parents=True)
            (target / 'state/relay/config.yml').write_text('private-relay')
            (target / 'state/uploader').mkdir()
            (target / 'state/uploader/openlist-token').write_text('legacy-token')
            database = target / 'state/uploader/state.sqlite3'
            with closing(sqlite3.connect(database)) as db:
                db.execute('CREATE TABLE uploads(id INTEGER PRIMARY KEY)')
                db.execute('INSERT INTO uploads VALUES (42)')
                db.commit()
            database.chmod(0o640)
            if os.name == 'posix' and os.geteuid() == 0:
                os.chown(database, 1000, 1000)
            if os.name == 'posix':
                (database.parent / 'alias.sqlite3').hardlink_to(database)
            (target / 'media').mkdir()
            (target / 'media/video.mkv').write_bytes(b'keep-video')
            for name in ('removed.js', 'local.js'):
                (target/'relay').mkdir(exist_ok=True)
                (target/'relay'/name).write_text(name)
            (source/'relay/added.js').write_text('new')
            (source/'relay/added.js').chmod(0o600)
            (source/'relay/custom-js').mkdir()
            (source/'relay/custom-js/search.js').write_text('new')
            (target/'relay/custom-js').mkdir(mode=0o700)
            old_manifest = json.dumps(['compose.yaml', 'relay/removed.js'])
            (target/update_files.MANIFEST).write_text(old_manifest)
            with patch('os.chown', create=True):
                backup = update_files.snapshot(source, target)
                update_files.apply(source, target, backup)
            self.assertEqual((target / '.env').read_text(), 'private-settings')
            self.assertEqual((target / 'state/relay/config.yml').read_text(), 'private-relay')
            self.assertEqual((target / 'media/video.mkv').read_bytes(), b'keep-video')
            self.assertEqual((target / 'compose.yaml').read_text(), 'new source')
            self.assertEqual((target / 'compose.override.yaml').read_text(), 'machine-specific-network-settings')
            self.assertEqual((target/'state/relay-token/openlist-token').read_text(), 'legacy-token')
            self.assertEqual((target/'relay/added.js').read_text(), 'new')
            if os.name == 'posix':
                self.assertEqual((target/'relay/added.js').stat().st_mode & 0o777, 0o644)
                self.assertEqual((target/'relay/custom-js').stat().st_mode & 0o777, 0o755)
            self.assertFalse((target/'relay/removed.js').exists())
            backups = list((target / 'backups').glob('*.tar.gz'))
            self.assertEqual(len(backups), 1)
            with tarfile.open(backups[0]) as tar:
                self.assertEqual(tar.extractfile('compose.yaml').read(), b'old source')
                self.assertEqual(tar.extractfile('.env').read(), b'private-settings')
                self.assertEqual(tar.extractfile('compose.override.yaml').read(), b'machine-specific-network-settings')
                self.assertFalse(any(n.startswith('media/') for n in tar.getnames()))
                member = tar.getmember(database.relative_to(target).as_posix())
                if os.name == 'posix':
                    self.assertEqual((member.mode, member.uid, member.gid),
                                     (0o640, database.stat().st_uid, database.stat().st_gid))
                    self.assertTrue(tar.getmember('state/uploader/alias.sqlite3').isfile())
                    self.assertTrue(member.isfile())
                restored_db = base / 'restored.sqlite3'
                restored_db.write_bytes(tar.extractfile(member).read())
                with closing(sqlite3.connect(restored_db)) as db:
                    self.assertEqual(db.execute('SELECT id FROM uploads').fetchall(), [(42,)])
            update_files.restore(target, backup)
            self.assertFalse((target/'relay/added.js').exists())
            self.assertEqual((target/'relay/removed.js').read_text(), 'removed.js')
            self.assertEqual((target/'relay/local.js').read_text(), 'local.js')
            self.assertEqual((target/'compose.yaml').read_text(), 'old source')
            self.assertEqual((target/update_files.MANIFEST).read_text(), old_manifest)

    def test_release_payload_contains_required_emby_fixtures(self):
        data = bundle.make_payload(ROOT)
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as tar:
            names = set(tar.getnames())
            self.assertTrue(bundle.FIXTURES <= names)
            self.assertNotIn('docs/DEPLOYMENT.md', names)

    def test_safe_unpack_and_resume_preserve_configuration(self):
        data = archive()
        digest = hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'cinema'
            previous_umask = os.umask(0o077)
            try:
                bundle.extract_payload(data, target, digest)
            finally:
                os.umask(previous_umask)
            if os.name == 'posix':
                self.assertEqual((target / 'relay/custom-js').stat().st_mode & 0o777, 0o755)
                self.assertEqual((target / 'relay').stat().st_mode & 0o777, 0o755)
                self.assertEqual((target / 'relay/custom-js/search.js').stat().st_mode & 0o777, 0o644)
            (target / '.env').write_text('PRIVATE=preserved')
            bundle.extract_payload(data, target, digest)
            self.assertEqual((target / '.env').read_text(), 'PRIVATE=preserved')
            with self.assertRaises(ValueError):
                bundle.extract_payload(data, Path(temp) / 'corrupt', '0' * 64)
            self.assertFalse((Path(temp) / 'corrupt').exists())
            if os.name == 'posix':
                (target / 'install.sh').unlink()
                (target / 'install.sh').symlink_to(target / 'configure.py')
                with self.assertRaisesRegex(ValueError, '符号链接'):
                    bundle.extract_payload(data, target, digest)

    def test_unsafe_archive_and_existing_directory_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            for name, kind in [('../escape', tarfile.REGTYPE), ('/escape', tarfile.REGTYPE), ('link', tarfile.SYMTYPE)]:
                data = archive(name, kind)
                with self.assertRaises(ValueError):
                    bundle.extract_payload(data, Path(temp) / 'cinema', hashlib.sha256(data).hexdigest())
                self.assertFalse((Path(temp) / 'cinema').exists())
            target = Path(temp) / 'existing'
            target.mkdir()
            (target / 'keep.txt').write_text('keep')
            data = archive()
            with self.assertRaises(ValueError):
                bundle.extract_payload(data, target, hashlib.sha256(data).hexdigest())
            self.assertEqual((target / 'keep.txt').read_text(), 'keep')

    def test_package_allowlist_excludes_secrets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in bundle.FILES:
                (root / name).write_text('package source\n', encoding='utf-8', newline='\n')
            for name in bundle.DIRECTORIES:
                (root / name).mkdir()
            (root / '.env').write_text('SECRET')
            (root / 'uploader/.env').write_text('SECRET')
            (root / 'uploader/private.key').write_text('SECRET')
            (root / 'uploader/main.py').write_text('print(1)')
            (root / 'scripts/build_all_in_one.py').write_text('print(1)')
            (root / 'scripts/update_portal.sh').write_text('#!/bin/bash\nexit 0\n', newline='\n')
            (root / 'portal/index.html').write_text('<html lang="zh-CN"></html>')
            (root / 'tests/test_installer.py').write_text('print(1)')
            (root / 'tests/search-entry.test.cjs').write_text('console.log(1)')
            (root / 'gateway/Caddyfile').write_text('example.com { respond "ok" }')
            (root / 'tests/.env').write_text('SECRET')
            data = bundle.make_payload(root)
            with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as tar:
                names = tar.getnames()
            self.assertIn('uploader/main.py', names)
            self.assertIn('scripts/build_all_in_one.py', names)
            self.assertIn('scripts/update_portal.sh', names)
            self.assertIn('portal/index.html', names)
            self.assertIn('.dockerignore', names)
            self.assertIn('tests/test_installer.py', names)
            self.assertIn('tests/search-entry.test.cjs', names)
            self.assertIn('gateway/Caddyfile', names)
            self.assertFalse(any('.env' in name or 'private.key' in name for name in names))
            result = bundle.build(root, root / 'dist/cinema-all-in-one.sh')
            contents = result.read_bytes()
            self.assertNotIn(b'\r', contents)
            embedded = base64.b64decode(contents.split(b'\n__CINEMA_PAYLOAD__\n', 1)[1])
            self.assertEqual(embedded, data)
            self.assertTrue(result.with_suffix('.sh.sha256').read_text().startswith(hashlib.sha256(contents).hexdigest()))
            python_source = contents.decode('utf-8').split("<<'PY_EXTRACT'\n", 1)[1].split('\nPY_EXTRACT', 1)[0]
            compile(python_source, 'embedded-extractor', 'exec')

    def test_port_conflicts_and_other_project_are_rejected(self):
        code = (ROOT / 'scripts/compose_preflight.py').read_text(encoding='utf-8')
        config = {'name': 'cinema', 'services': {'emby': {'ports': [{'published': '8096', 'host_ip': '127.0.0.1'}]}}}
        def outputs(args, **kwargs):
            if 'config' in args:
                return json.dumps(config)
            if 'inspect' in args:
                return json.dumps([{'Config': {'Labels': {'com.docker.compose.project.working_dir': '/another-install'}}}])
            return ''
        with patch('sys.argv', ['-', 'emby']), patch('subprocess.check_output', side_effect=outputs):
            with patch('socket.socket') as sock:
                sock.return_value.__enter__.return_value.bind.side_effect = OSError('occupied')
                with self.assertRaisesRegex(SystemExit, '端口预检'):
                    exec(compile(code, 'port-preflight', 'exec'), {})
        def other_project(args, **kwargs):
            if args[1:3] == ('ps', '-aq'):
                return 'other'
            return outputs(args, **kwargs)
        with patch('sys.argv', ['-']), patch('subprocess.check_output', side_effect=other_project):
            with self.assertRaisesRegex(SystemExit, '另一部署目录'):
                exec(compile(code, 'project-preflight', 'exec'), {})


if __name__ == '__main__':
    unittest.main()
