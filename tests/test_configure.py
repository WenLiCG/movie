import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

PROJECT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("configure", PROJECT / "configure.py")
configure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(configure)


class ConfigurationTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "Unix permission semantics")
    def test_private_umask_does_not_make_bind_mounts_inaccessible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "relay").mkdir()
            (root / "relay/config.example.yml").write_text(
                (PROJECT / "relay/config.example.yml").read_text(encoding="utf-8"), encoding="utf-8")
            old_mask = os.umask(0o077)
            try:
                configure.prepare(root, "movie.example.com")
            finally:
                os.umask(old_mask)
            for name in configure.DIRECTORIES:
                info = (root / name).stat()
                self.assertEqual(info.st_mode & 0o777, 0o750, name)
                if os.geteuid() == 0:
                    self.assertEqual((info.st_uid, info.st_gid), (1000, 1000), name)

    def test_domain_rejects_configuration_injection(self):
        for value in ("https://movie.example.com", "a.example.com\nX=y", "a.example.com:443",
                      "a..com", "-host.example.com", "127.0.0.1", "a.com/path", "*.example.com"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                configure.domain(value)
        self.assertEqual(configure.domain("Movie.Example.COM"), "movie.example.com")

    def test_fresh_config_secrets_and_existing_install_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "relay").mkdir()
            (root / "relay/config.example.yml").write_text(
                (PROJECT / "relay/config.example.yml").read_text(encoding="utf-8"), encoding="utf-8")
            configure.prepare(root, "movie.example.com")
            config_path = root / "state/relay/config.yml"
            before = config_path.read_bytes()
            self.assertNotIn(b"__RELAY_SECRET__", before)
            self.assertNotIn(b"__RELAY_SECRET__", (root / ".env").read_bytes())
            self.assertEqual((root / '.env').read_text(), 'MOVIE_DOMAIN=movie.example.com\n')
            with self.assertRaises(ValueError):
                configure.prepare(root, "other.example.com")
            self.assertEqual(before, config_path.read_bytes())
            token = 'test-token-with-quote"and\\slash'
            configure.set_token(root, token)
            line = next(x for x in config_path.read_text().splitlines() if x.startswith("  token: "))
            self.assertEqual(json.loads(line.removeprefix("  token: ")), token)
            with self.assertRaises(ValueError):
                configure.set_token(root, "replacement")

    def test_does_not_adopt_existing_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "media").mkdir()
            sentinel = root / "media/existing.mp4"
            sentinel.write_bytes(b"existing-data")
            with self.assertRaises(ValueError):
                configure.prepare(root, "movie.example.com")
            self.assertEqual(sentinel.read_bytes(), b"existing-data")
            self.assertFalse((root / ".env").exists())

    def test_public_requires_authentication_and_completed_emby_setup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "state/emby/config").mkdir(parents=True)
            config = root / "state/emby/config/system.xml"
            config.write_text("<ServerConfiguration><IsStartupWizardCompleted>true</IsStartupWizardCompleted></ServerConfiguration>")
            with patch("integration.read_api_key", return_value="a" * 32), patch("integration.Client") as client:
                for host in ({"authenticationMethod": "none", "authenticationRequired": "enabled"},
                             {"authenticationMethod": "forms", "authenticationRequired": "disabledForLocalAddresses"},
                             {"authenticationMethod": "forms", "authenticationRequired": "enabled", "trustedNetworks": "0.0.0.0/0"}):
                    client.return_value.json.return_value = host
                    with self.assertRaises(ValueError):
                        configure.check_public(root)
                client.return_value.json.side_effect = lambda method, path: (
                    {"authenticationMethod": "forms", "authenticationRequired": "enabled"} if path.endswith('/config/host') else
                    {"code": 200, "data": {"initialized": True}} if path.endswith('/init_status') else {"code": 401})
                client.return_value.request.side_effect = ValueError('HTTP 403')
                with patch("builtins.print"), patch("builtins.input", return_value="admin"), \
                        patch("configure.getpass.getpass", return_value="test-password"), \
                        patch("integration.verify_emby_accounts") as verify:
                    configure.check_public(root)
                    verify.assert_called_once_with("admin", "test-password")
                config.write_text("<ServerConfiguration><IsStartupWizardCompleted>false</IsStartupWizardCompleted></ServerConfiguration>")
                with self.assertRaises(ValueError):
                    configure.check_public(root)

    def test_public_blocks_unprotected_management_apps_before_emby_login(self):
        for unsafe in ('sonarr', 'openlist', 'qbittorrent'):
            def make_client(base, key=None):
                client = Mock()
                client.json.side_effect = lambda method, path: (
                    {"authenticationMethod": "none" if unsafe == 'sonarr' and '/sonarr' in base else "forms",
                     "authenticationRequired": "enabled"} if path.endswith('/config/host') else
                    {"code": 200, "data": {"initialized": True}} if path.endswith('/init_status') else
                    {"code": 200 if unsafe == 'openlist' else 401})
                if unsafe != 'qbittorrent':
                    client.request.side_effect = ValueError('HTTP 401')
                return client
            with self.subTest(app=unsafe), patch('integration.read_api_key', return_value='a' * 32), \
                    patch('integration.Client', side_effect=make_client), patch('builtins.input') as prompt:
                with self.assertRaises(ValueError):
                    configure.check_public(Path('/unused'))
                prompt.assert_not_called()

    @unittest.skipUnless(os.name == "posix", "Unix permission semantics")
    def test_atomic_secret_file_has_service_owner_and_private_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            configure.private_write(path, '{"key": "test"}\n')
            configure.private_write(path, '{"key": "rotated"}\n')
            self.assertEqual(json.loads(path.read_text()), {"key": "rotated"})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            if os.geteuid() == 0:
                self.assertEqual(path.stat().st_uid, 1000)


if __name__ == "__main__":
    unittest.main()
