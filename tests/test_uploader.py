import contextlib
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path, PurePosixPath
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch, Mock
from urllib.parse import parse_qs, unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "uploader"))
from uploader import Failure, HTTP, OpenList, Sources, Uploader, candidate, local_path, single_process


class Service(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.objects = {"/aliyun": {"is_dir": True, "size": 0}}
        self.calls, self.puts, self.moves, self.read_sizes = [], 0, 0, []
        self.put_mode, self.move_mode = "success", "success"
        self.task_state = 2
        self.torrents, self.files, self.series, self.episodes = [], [], [], []
        self.login_body, self.login_cookie = b"Ok.", "SID=test-session; path=/"
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_port}"

    def close(self):
        self.shutdown()
        self.server_close()
        self.thread.join()

    def finish_move(self, body):
        name = body["names"][0]
        src, dst = body["src_dir"] + "/" + name, body["dst_dir"] + "/" + name
        if dst in self.objects:
            return False
        self.objects[dst] = self.objects.pop(src)
        return True


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, value, code=200, headers=None):
        raw = value if isinstance(value, bytes) else json.dumps(value).encode()
        self.send_response(code)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def disconnect(self):
        self.connection.shutdown(socket.SHUT_RDWR)
        self.connection.close()

    def do_GET(self):
        path = urlsplit(self.path).path
        self.server.calls.append(("GET", path))
        value = {"/api/v2/torrents/info": self.server.torrents,
                 "/api/v2/torrents/files": self.server.files,
                 "/api/v3/series": self.server.series,
                 "/api/v3/episodefile": self.server.episodes}.get(path)
        if path == "/redirect":
            return self.reply(b"", 302, {"Location": self.server.url + "/credential-trap"})
        self.reply(value)

    def do_POST(self):
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        if self.path.startswith('/api/admin/task/upload/info?'):
            return self.reply({'code':200,'data':{'id':'native-upload','state':self.server.task_state,'progress':100 if self.server.task_state == 2 else 35}})
        if self.path == "/api/v2/auth/login":
            assert parse_qs(raw.decode())["username"] == ["test-user"]
            headers = {"Set-Cookie": self.server.login_cookie} if self.server.login_cookie else {}
            return self.reply(self.server.login_body, headers=headers)
        body = json.loads(raw)
        self.server.calls.append(("POST", self.path, body))
        data, code = None, 200
        if self.path == "/api/fs/list":
            if body["path"] not in self.server.objects:
                code = 500
            else:
                entries = [{**value, "name": PurePosixPath(path).name}
                           for path, value in self.server.objects.items()
                           if str(PurePosixPath(path).parent) == body["path"]]
                offset = (body["page"] - 1) * body["per_page"]
                data = {"content": entries[offset:offset + body["per_page"]], "total": len(entries)}
        elif self.path == "/api/fs/mkdir":
            self.server.objects[body["path"]] = {"is_dir": True, "size": 0}
        elif self.path == "/api/fs/move":
            assert body["overwrite"] is False and body["skip_existing"] is False
            self.server.moves += 1
            self.server.last_move = body
            if self.server.move_mode == "async":
                data = {"tasks": [{"id": "pending-task"}]}
            elif not self.server.finish_move(body):
                code = 403
            elif self.server.move_mode == "disconnect":
                return self.disconnect()
        else:
            code = 500
        self.reply({"code": code, "message": "sensitive remote details must not be logged", "data": data})

    def do_PUT(self):
        self.server.puts += 1
        assert self.headers["As-Task"] == "true"
        assert self.headers["Overwrite"] == "false"
        path = unquote(self.headers["File-Path"])
        remaining = int(self.headers["Content-Length"])
        size, digest = 0, hashlib.sha1()
        while remaining:
            chunk = self.rfile.read(min(65536, remaining))
            if not chunk:
                break
            self.server.read_sizes.append(len(chunk))
            size += len(chunk)
            remaining -= len(chunk)
            digest.update(chunk)
            if self.server.put_mode == "disconnect":
                self.server.objects[path] = {"size": size, "is_dir": False,
                                             "hash_info": {"sha1": digest.hexdigest()}}
                return self.disconnect()
        assert digest.hexdigest() == self.headers["X-File-Sha1"]
        remote_hash = "0" * 40 if self.server.put_mode == "bad_hash" else digest.hexdigest()
        self.server.objects[path] = {"size": size, "is_dir": False, "hash_info": {"sha1": remote_hash}}
        self.reply({"code": 500 if self.server.put_mode == "business_error" else 200, "data": {'task':{'id':'native-upload'}}})


class UploaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.media, self.state = self.base / "media", self.base / "state"
        self.movie_root = self.media / "downloads" / "movies"
        self.tv_root = self.media / "library" / "tv"
        self.movie_root.mkdir(parents=True)
        self.tv_root.mkdir(parents=True)
        self.state.mkdir()
        self.source = self.movie_root / "Movie.mkv"
        self.source.write_bytes(b"frame-data" * 300000)
        self.item = candidate("movie:123:0", self.source, "/aliyun/Movies/Movie/Movie.mkv", self.source.stat().st_size)
        self.service = Service()
        self.remote = OpenList(self.service.url, "test-token-not-for-logs")
        self.uploader = Uploader(self.remote, self.state, self.media)

    def tearDown(self):
        self.uploader.db.close()
        self.service.close()
        self.temp.cleanup()

    def row(self):
        return dict(self.uploader.db.execute("SELECT * FROM jobs").fetchone())

    def run_upload(self, items=None):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.uploader.run([self.item] if items is None else items)
        self.assertNotIn("test-token", output.getvalue())
        self.assertNotIn("sensitive remote", output.getvalue())
        return output.getvalue()

    def restart(self):
        self.uploader.db.close()
        self.uploader = Uploader(self.remote, self.state, self.media)
        self.uploader.db.execute("UPDATE jobs SET retry_at=0")
        self.uploader.db.commit()

    def test_success_streams_and_does_not_reupload(self):
        self.run_upload()
        row = self.row()
        self.assertEqual(row["state"], "published")
        self.assertEqual(json.loads(row["receipt"])["put_code"], 200)
        self.assertIn(self.item["dest"], self.service.objects)
        self.assertNotIn(row["stage"], self.service.objects)
        self.assertTrue(self.source.exists())
        self.assertGreater(len(self.service.read_sizes), 40)
        self.restart()
        self.run_upload()
        self.assertEqual((self.service.puts, self.service.moves), (1, 1))

    def test_native_background_upload_is_not_published_until_task_succeeds(self):
        self.service.task_state = 1
        self.run_upload()
        self.assertEqual(self.row()['state'], 'queued')
        self.assertEqual(self.row()['progress'], 35)
        self.assertIsNone(self.row()['receipt'])
        self.assertEqual(self.service.moves, 0)
        self.service.objects[self.row()['stage']].pop('hash_info')
        self.restart()
        self.run_upload()
        self.assertEqual(self.service.puts, 1, 'restart must poll the same task, not retransmit the file')
        self.service.task_state = 2
        self.run_upload()
        self.assertEqual(self.row()['state'], 'published')
        self.assertEqual(self.service.puts, 1)

    def test_upload_task_recovered_by_name_requires_sha1_even_after_restart(self):
        self.service.task_state = 1
        original_put = self.remote.put

        def lose_response(*args):
            original_put(*args)
            raise Failure('network_or_protocol')

        with patch.object(self.remote, 'put', side_effect=lose_response):
            self.run_upload()
        job = self.row()
        self.assertIsNone(job['task_id'])
        stage = PurePosixPath(job['stage'])
        listing = self.service.objects[str(stage)]
        hashes = listing.pop('hash_info')
        name = f"upload {stage.name} to [/aliyun]({str(stage.parent).removeprefix('/aliyun')})"
        with patch.object(self.remote, 'tasks', return_value=[{'id': 'native-upload', 'name': name}]):
            self.restart()
            self.run_upload([])
        self.assertEqual(self.row()['task_id'], 'native-upload')

        self.service.task_state = 2
        self.restart()
        self.run_upload([])
        self.assertEqual((self.row()['state'], self.row()['error']), ('queued', 'upload_unconfirmed'))
        self.assertIsNone(self.row()['receipt'])
        self.assertEqual((self.service.puts, self.service.moves), (1, 0))

        listing['hash_info'] = hashes
        self.restart()
        self.run_upload([])
        self.assertEqual(self.row()['state'], 'published')
        self.assertEqual((self.service.puts, self.service.moves), (1, 1))

    def test_queued_upload_from_older_database_requires_sha1(self):
        self.service.task_state = 1
        self.run_upload()
        job = self.row()
        self.service.objects[job['stage']].pop('hash_info')
        with self.uploader.db:
            self.uploader.db.execute('ALTER TABLE jobs DROP COLUMN task_id_from_put')
        self.service.task_state = 2

        self.restart()
        self.run_upload([])

        self.assertEqual(self.row()['task_id'], job['task_id'])
        self.assertEqual((self.row()['state'], self.row()['error']), ('queued', 'upload_unconfirmed'))
        self.assertIsNone(self.row()['receipt'])
        self.assertEqual(self.service.moves, 0)

    def test_failed_native_task_never_creates_a_publish_receipt(self):
        self.service.task_state = 7
        self.run_upload()
        self.assertEqual(self.row()['state'], 'pending')
        self.assertEqual(self.row()['restart_count'], 1)
        self.assertIsNone(self.row()['receipt'])
        self.assertEqual(self.service.moves, 0)

    def test_other_files_wait_without_showing_an_old_transfer_error(self):
        self.service.task_state = 1
        self.run_upload()
        other = candidate('movie:456:0', self.source, '/aliyun/Movies/Other/Movie.mkv', self.source.stat().st_size)
        self.uploader.add(other)
        self.uploader.update(other, state='uploading', error='network_or_protocol')
        self.run_upload([self.item, other])
        waiting = self.uploader.db.execute('SELECT state,error FROM jobs WHERE id=?', (other['id'],)).fetchone()
        self.assertEqual(tuple(waiting), ('uploading', 'upload_unconfirmed'))
        self.assertEqual(self.service.puts, 1)

    def test_queue_acceptance_alone_is_not_a_publish_receipt(self):
        self.service.task_state = 1
        self.run_upload()
        job = self.row()
        self.uploader.update(job, receipt=json.dumps({'stage':job['stage'],'size':job['size'],'sha1':job['sha1'],
                                                     'put_code':200,'task_id':job['task_id']}))
        with self.assertRaisesRegex(Failure, 'missing_upload_receipt'):
            self.uploader.publish(job)
        self.assertEqual(self.service.moves, 0)

    def test_failed_native_task_does_not_block_other_files(self):
        self.service.task_state = 7
        self.run_upload()
        other = candidate('movie:456:0', self.source, '/aliyun/Movies/Other/Movie.mkv', self.source.stat().st_size)
        self.run_upload([self.item, other])
        self.assertEqual(self.service.puts, 2)

    def test_background_cache_space_guard_keeps_local_source(self):
        with patch('uploader.shutil.disk_usage', return_value=Mock(free=0)):
            self.run_upload()
        self.assertEqual(self.row()['error'], 'upload_cache_space_low')
        self.assertEqual(self.service.puts, 0)
        self.assertTrue(self.source.exists())

    def test_interrupted_upload_has_no_receipt_and_never_publishes_partial(self):
        self.service.put_mode = "disconnect"
        self.run_upload()
        old = self.row()
        self.assertEqual(old["state"], "uploading")
        self.assertIsNone(old["receipt"])
        self.assertEqual(self.service.moves, 0)
        self.assertNotIn(self.item["dest"], self.service.objects)
        self.service.put_mode = "success"
        old_instance = "11111111-1111-1111-1111-111111111111"
        self.uploader.update(dict(old),instance_id=old_instance)
        self.restart()
        self.uploader.instance=lambda:"22222222-2222-2222-2222-222222222222"
        self.run_upload()
        self.uploader.db.execute("UPDATE jobs SET retry_at=0");self.uploader.db.commit()
        self.run_upload()
        self.assertEqual(self.row()["state"], "published")
        self.assertIn(old["stage"], self.service.objects)  # quarantined, never reused or deleted
        self.assertNotEqual(old["stage"], self.row()["stage"])

    def test_http_200_business_error_is_not_upload_success(self):
        self.service.put_mode = "business_error"
        self.run_upload()
        self.assertEqual(self.row()["error"], "openlist_code_500")
        self.assertIsNone(self.row()["receipt"])
        self.assertEqual(self.service.moves, 0)

    def test_bad_remote_hash_prevents_receipt_and_publish(self):
        self.service.put_mode = "bad_hash"
        self.run_upload()
        job = self.row()
        self.assertEqual(job["error"], "remote_hash_mismatch")
        self.assertIsNone(job["receipt"])
        self.assertEqual(self.service.moves, 0)

        # A completed native transfer waiting on verification must not occupy the upload slot.
        self.service.put_mode = "success"
        other = candidate("movie:456:0", self.source, "/aliyun/Movies/Other/Movie.mkv", self.item["size"])
        self.run_upload([self.item, other])
        self.assertIn(other["dest"], self.service.objects)
        self.assertNotIn(self.item["dest"], self.service.objects)
        self.assertTrue(self.source.exists())
        self.assertEqual((self.service.puts, self.service.moves), (2, 1))

        self.service.objects[job["stage"]]["hash_info"]["sha1"] = job["sha1"]
        self.restart()
        self.run_upload([])
        self.assertEqual((self.service.puts, self.service.moves), (2, 2))
        self.assertIn(self.item["dest"], self.service.objects)

    def test_recovered_task_cannot_inherit_completion_from_missing_task(self):
        self.service.put_mode = "bad_hash"
        self.run_upload()
        job = self.row()
        stage = PurePosixPath(job["stage"])
        name = f"upload {stage.name} to [/aliyun]({str(stage.parent).removeprefix('/aliyun')})"
        self.restart()
        other = candidate("movie:456:0", self.source, "/aliyun/Movies/Other/Movie.mkv", self.item["size"])
        with patch.object(self.remote, "tasks", return_value=[{"id": "replacement", "name": name}]), \
             patch.object(self.remote, "upload_task", side_effect=[Failure("http_404"), Failure("network_or_protocol")]):
            self.run_upload([self.item, other])
        current = self.uploader.db.execute("SELECT task_id,native_state FROM jobs WHERE id=?", (job["id"],)).fetchone()
        self.assertEqual(tuple(current), ("replacement", None))
        self.assertEqual(self.service.puts, 1)

    def test_missing_task_loses_completion_before_listing_replacements(self):
        self.service.put_mode = "bad_hash"
        self.run_upload()
        job = self.row()
        stage = PurePosixPath(job["stage"])
        name = f"upload {stage.name} to [/aliyun]({str(stage.parent).removeprefix('/aliyun')})"
        duplicates = [{"id": str(i), "name": name} for i in range(2)]
        other = candidate("movie:456:0", self.source, "/aliyun/Movies/Other/Movie.mkv", self.item["size"])
        for listing, expected in ((duplicates, "ambiguous_upload_task"), (Failure("network_or_protocol"), "network_or_protocol")):
            with self.subTest(expected=expected):
                self.uploader.update(job, retry_at=0, native_state=2, terminal_proven=1)
                with patch.object(self.remote, "upload_task", side_effect=Failure("http_404")), \
                     patch.object(self.remote, "tasks", side_effect=[listing]):
                    self.run_upload([self.item, other])
                    current = dict(self.uploader.db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone())
                    self.assertEqual((current["native_state"], current["terminal_proven"], current["error"]), (None, 0, expected))
                    with self.assertRaisesRegex(Failure, "upload_unconfirmed"):
                        self.uploader.cancel_job(current)
                self.assertEqual(self.service.puts, 1)

    def test_restart_after_lost_move_response_recovers_without_resubmission(self):
        self.service.move_mode = "disconnect"
        self.run_upload()
        self.assertEqual(self.row()["state"], "moving")
        self.assertIsNotNone(self.row()["receipt"])
        self.restart()
        self.run_upload([])  # Recovery uses durable evidence even if discovery is unavailable.
        self.assertEqual(self.row()["state"], "published")
        self.assertEqual((self.service.puts, self.service.moves), (1, 1))

    def test_restart_after_durable_upload_receipt_continues_publication(self):
        self.uploader.add(self.item)
        job = self.row()
        self.remote.mkdir(str(PurePosixPath(job["dest"]).parent))
        self.uploader.upload(job)
        self.assertEqual(self.row()["state"], "uploaded")
        self.assertEqual(self.service.moves, 0)
        self.restart()
        self.run_upload([])
        self.assertEqual(self.row()["state"], "published")
        self.assertEqual((self.service.puts, self.service.moves), (1, 1))

    def test_legacy_upload_receipt_without_task_origin_requires_sha1_before_move(self):
        self.uploader.add(self.item)
        self.remote.mkdir(str(PurePosixPath(self.item['dest']).parent))
        self.uploader.upload(self.row())
        job = self.row()
        stage = self.service.objects[job['stage']]
        hashes = stage.pop('hash_info')
        with self.uploader.db:
            self.uploader.db.execute('ALTER TABLE jobs DROP COLUMN task_id_from_put')

        self.restart()
        self.run_upload([])

        self.assertEqual((self.row()['state'], self.row()['error']), ('uploaded', 'remote_hash_unavailable'))
        self.assertEqual(self.service.moves, 0)
        stage['hash_info'] = hashes
        self.restart()
        self.run_upload([])
        self.assertEqual(self.row()['state'], 'published')
        self.assertEqual(json.loads(self.row()['receipt'])['evidence'], 'remote_sha1')

    def test_new_target_before_move_is_preserved(self):
        original_put = self.remote.put

        def put_then_conflict(*args):
            task_id = original_put(*args)
            self.service.objects[self.item["dest"]] = {"is_dir": False, "size": 123}
            return task_id

        self.remote.put = put_then_conflict
        self.run_upload()
        self.assertEqual(self.row()["state"], "uploaded")
        self.assertEqual(self.row()["error"], "destination_conflict")
        self.assertEqual(self.service.moves, 0)
        self.assertEqual(self.service.objects[self.item["dest"]]["size"], 123)
        self.assertIn(self.row()["stage"], self.service.objects)

    def test_source_change_after_upload_blocks_publication(self):
        original_put = self.remote.put

        def put_then_change(*args):
            original_put(*args)
            with self.source.open("ab") as handle:
                handle.write(b"changed")

        self.remote.put = put_then_change
        self.run_upload()
        self.assertIsNone(self.row()["receipt"])
        self.assertEqual(self.row()["error"], "source_changed")
        self.assertEqual(self.service.moves, 0)

    def test_async_move_waits_for_verified_remote_result(self):
        self.service.move_mode = "async"
        self.run_upload()
        self.assertEqual(self.row()["state"], "moving")
        self.restart()
        self.run_upload([])
        self.assertEqual(self.service.moves, 1)
        self.assertEqual(self.row()["state"], "moving")
        self.service.finish_move(self.service.last_move)
        self.restart()
        self.run_upload([])
        self.assertEqual(self.row()["state"], "published")

    def test_existing_same_name_is_preserved_even_if_same_size_and_hash(self):
        self.remote.mkdir("/aliyun/Movies/Movie")
        existing = {"is_dir": False, "size": self.item["size"],
                    "hash_info": {"sha1": hashlib.sha1(self.source.read_bytes()).hexdigest()}}
        self.service.objects[self.item["dest"]] = existing.copy()
        self.run_upload()
        self.assertEqual(self.row()["error"], "destination_conflict")
        self.assertEqual(self.service.objects[self.item["dest"]], existing)
        self.assertEqual((self.service.puts, self.service.moves), (0, 0))

    def test_paths_and_symlinks_cannot_escape_media(self):
        for dest in ("/aliyun/Movies/../escape.mkv", "/aliyun/TV//x.mkv", "/other/x.mkv"):
            with self.assertRaises(Failure):
                candidate("bad", self.source, dest, 1)
        outside = self.base / "outside.mkv"
        outside.write_bytes(b"private")
        with self.assertRaises(Failure):
            local_path(self.movie_root / ".." / ".." / ".." / "outside.mkv", self.movie_root)
        self.item["source"] = str(outside)
        self.run_upload()
        self.assertEqual(self.service.puts, 0)
    def test_symlink_sources_and_roots_are_rejected(self):
        outside = self.base / "outside.mkv"
        outside.write_bytes(b"private")
        link = self.movie_root / "link.mkv"
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("this Windows account cannot create symlinks; run on Linux for this check")
        with self.assertRaises(Failure):
            local_path(link, self.movie_root)
        linked_root = self.base / "linked-root"
        linked_root.symlink_to(self.movie_root, target_is_directory=True)
        with self.assertRaises(Failure):
            local_path(linked_root / self.source.name, linked_root)

    def test_redirect_is_not_followed_with_credentials(self):
        with self.assertRaisesRegex(Failure, "http_302"):
            HTTP(self.service.url, {"Authorization": "secret"}).request("GET", "/redirect")
        self.assertNotIn(("GET", "/credential-trap"), self.service.calls)
        for url in ("file:///etc/passwd", "http://user:password@example.com", "http://example.com?key=secret"):
            with self.assertRaises(Failure):
                HTTP(url)

    def test_only_completed_selected_movies_and_imported_episodefiles(self):
        self.service.torrents = [{"hash": "abc", "name": "Movie", "save_path": str(self.movie_root),
                                  "category": "cinema-movies", "progress": 1, "amount_left": 0, "state": "uploading"}]
        self.service.files = [{"index": 0, "name": "Movie.mkv", "size": self.item["size"], "priority": 1, "progress": 1}]
        series = self.tv_root / "Show"
        series.mkdir()
        episode = series / "S01E01.mkv"
        episode.write_bytes(b"episode")
        (series / "S01E02.mkv").write_bytes(b"still copying, not in Sonarr")
        self.service.series = [{"id": 1, "path": str(series)}]
        self.service.episodes = [{"id": 9, "seriesId": 1, "path": str(episode),
                                  "relativePath": "S01E01.mkv", "size": 7}]
        config = {"qb_url": self.service.url, "qb_username": "test-user", "qb_password": "password",
                  "sonarr_url": self.service.url, "sonarr_api_key": "sonarr-secret"}
        sources = Sources(config, self.media)
        self.assertEqual(len(sources.downloads()), 1)
        stale = {**self.service.files[0], "name": "missing.mkv", "index": 1}
        self.service.files.insert(0, stale)
        self.assertEqual([c['source'] for c in sources.downloads()], [str(self.source)])
        self.service.files.pop(0)
        self.service.files[0]["progress"] = 0.999
        self.assertEqual(sources.downloads(), [])
        self.service.files[0]["progress"] = 1
        self.service.files[0]["priority"] = 0
        self.assertEqual(sources.downloads(), [])
        tv = sources.tv()
        self.assertEqual(len(tv), 1)
        self.assertEqual(tv[0]["dest"], "/aliyun/TV/Show/S01E01.mkv")

    def test_manual_tv_download_archives_without_sonarr_and_never_as_a_movie(self):
        root=self.media/'downloads/tv-manual';root.mkdir(parents=True)
        source=root/'Variety.S01E01.mkv';source.write_bytes(b'variety episode')
        self.service.torrents=[{'hash':'abc','name':'Variety','save_path':str(root),'category':'cinema-tv',
                               'progress':1,'amount_left':0,'state':'uploading'}]
        self.service.files=[{'index':0,'name':source.name,'size':source.stat().st_size,'priority':1,'progress':1}]
        sources=Sources({'qb_url':self.service.url,'qb_username':'test-user','qb_password':'password',
                         'sonarr_url':self.service.url,'sonarr_api_key':'test-key'},self.media)
        candidates=sources.downloads()
        self.assertEqual(len(candidates),1)
        self.assertEqual(candidates[0]['dest'],'/aliyun/TV/Variety/Variety.S01E01.mkv')
        self.assertTrue(candidates[0]['identity'].startswith('manual-tv:'))
        self.run_upload(candidates)
        self.assertEqual(self.uploader.db.execute('SELECT state FROM jobs').fetchone()[0],'published')
        self.assertTrue(source.exists())
        self.assertFalse(any(c[1].startswith('/api/v3/') for c in self.service.calls))
        self.service.torrents[0]['save_path']=str(self.movie_root)
        self.assertEqual(sources.downloads(),[])

    def test_qb_53_login_and_reused_session_require_a_real_session_cookie(self):
        config = {"qb_url": self.service.url, "qb_username": "test-user", "qb_password": "password",
                  "sonarr_url": self.service.url, "sonarr_api_key": "test-key"}
        sources = Sources(config, self.media)
        self.service.login_body, self.service.login_cookie = b"", "QBT_SID_8080=test-session; path=/"
        self.assertEqual(sources.downloads(), [])
        self.assertEqual(sources.qb.headers["Cookie"], "QBT_SID_8080=test-session")
        self.service.login_cookie = None
        self.assertEqual(sources.downloads(), [], "an existing authenticated session need not set another cookie")
        sources.qb.headers.pop("Cookie")
        with self.assertRaisesRegex(Failure, "qb_login_failed"):
            sources.downloads()
        self.service.login_body, self.service.login_cookie = b"Fails.", "SID=invalid"
        with self.assertRaisesRegex(Failure, "qb_login_failed"):
            sources.downloads()

    def test_single_process_lock(self):
        with single_process(self.state):
            with self.assertRaisesRegex(Failure, "already_running"):
                with single_process(self.state):
                    self.fail("second uploader acquired lock")


if __name__ == "__main__":
    unittest.main()
