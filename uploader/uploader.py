"""Publish completed videos; clean local copies only after confirmed cloud publication."""

import contextlib
import hashlib
import http.client
import http.cookies
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import shutil
import stat
import sys
import time
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from archive import CLEANUP_EXTRAS, VIDEO, video, subtitle_for, create_archive, record_archive, archived_keys, watch_lock, read_requests, save_requests, apply_scope, task_actions, action_matches, delete_target, torrent_instance, episode_keys, TaskScopeChanged, episodes_with_monitoring, cloud_mount
from configure import private_write
from urllib.parse import quote, urlencode, urlsplit


CHUNK = 1024 * 1024
RESPONSE_LIMIT = 8 * CHUNK


class Failure(Exception):
    """Only fixed, non-secret status codes may be logged."""


def log(task, status):
    print(f"{task} {status}", flush=True)


def relative(value):
    if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value):
        raise Failure("unsafe_path")
    if "\\" in value or ":" in value or value.startswith("/"):
        raise Failure("unsafe_path")
    parts = value.split("/")
    if any(p in {"", ".", ".."} for p in parts):
        raise Failure("unsafe_path")
    return PurePosixPath(*parts)


def remote_path(value, mounts, folders=None):
    if not isinstance(value, str) or not value.startswith('/'):
        raise Failure('unsafe_remote_path')
    path = PurePosixPath('/') / relative(value[1:])
    if '/' + path.parts[1] not in mounts or (folders and (len(path.parts) < 4 or path.parts[2] not in folders)):
        raise Failure('unsafe_remote_path')
    return path


def configured_mounts(config):
    current = cloud_mount(config.get('cloud_mount', '/aliyun'))
    history = config.get('cloud_mounts', [])
    if not isinstance(history, list):
        raise Failure('invalid_configuration')
    return current, list(dict.fromkeys(cloud_mount(v) for v in ['/aliyun', *history, current]))


def local_path(value, root, missing_ok=False):
    path = Path(value)
    try:
        if not path.is_absolute():
            raise ValueError()
        parts = path.relative_to(root).parts
        if ".." in parts or not parts:
            raise ValueError()
        relative("/".join(parts))
        if root.resolve(strict=True) != root.absolute() or not path.resolve(strict=not missing_ok).is_relative_to(root):
            raise ValueError()
        current = root
        for part in parts:
            current = current / part
            if current.is_symlink():
                raise ValueError()
    except (ValueError, OSError, RuntimeError):
        raise Failure("unsafe_or_missing_source") from None
    return path


def series_sidecars(sources, episodes):
    # Sonarr can associate extras anywhere in a series by episode ID, regardless of name.
    by_series = {}
    # Sonarr excludes video formats from its existing-extra importer.
    excluded = VIDEO | CLEANUP_EXTRAS
    for episode in episodes:
        by_series.setdefault(episode['series_id'], {})[episode['file_id']] = Path(episode['path'])
    for series_id, expected in by_series.items():
        files = sources.sonarr.json('GET', '/api/v3/episodefile', query={'seriesId': series_id})
        if not any(f['id'] in expected for f in files):
            continue  # A previous attempt already removed the native episode files.
        series = sources.sonarr.json('GET', '/api/v3/series/' + str(series_id))
        if series['id'] != series_id:
            raise Failure('cleanup_episode_changed')
        root = local_path(series['path'], sources.media/'library/tv')
        native_paths = set()
        for f in files:
            path = local_path(f['path'], root, missing_ok=True)
            if f['seriesId'] != series_id or (f['id'] in expected and path != expected[f['id']]):
                raise Failure('cleanup_episode_changed')
            native_paths.add(path)
        directories = [root]
        while directories:
            with os.scandir(directories.pop()) as entries:
                for entry in entries:
                    path = Path(entry.path)
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISLNK(info.st_mode) or (os.name == 'nt' and info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                        raise Failure('unsafe_or_missing_source')
                    if stat.S_ISDIR(info.st_mode):
                        directories.append(path)
                    elif not stat.S_ISREG(info.st_mode):
                        raise Failure('unsafe_or_missing_source')
                    elif path not in native_paths and path.suffix.lower() not in excluded:
                        yield path


@contextlib.contextmanager
def open_source(path, root):
    path = local_path(path, root)
    if os.name == "posix":
        # Walk beneath the fixed mount without following symlinks, including during races.
        fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            parts = path.parts[1:]
            for index, part in enumerate(parts):
                flags = os.O_RDONLY | os.O_NOFOLLOW
                if index < len(parts) - 1:
                    flags |= os.O_DIRECTORY
                next_fd = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            handle = os.fdopen(fd, "rb")
            fd = None
        finally:
            if fd is not None:
                os.close(fd)
    else:
        # Windows is for local tests; the deployed Linux walk above is race-resistant.
        handle = path.open("rb")
    with handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise Failure("source_not_regular")
        yield handle


def fingerprint(handle):
    s = os.fstat(handle.fileno())
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def matches_payload(path, root, job):
    with open_source(path, root) as handle:
        before = fingerprint(handle)
        if before[2] != job['size'] or hashlib.file_digest(handle, 'sha1').hexdigest() != job['sha1']:
            return False
        current = path.stat(follow_symlinks=False)
        return (current.st_dev, current.st_ino) == before[:2] and fingerprint(handle) == before


def recheck_series_sidecars(sources, episodes, verified):
    # Hashing a torrent or waiting for an Emby scan can take long enough for a new import.
    for path in series_sidecars(sources, episodes):
        if path not in verified or not matches_payload(path, *verified[path]):
            raise Failure('cleanup_local_unverified')


def torrent_paths(torrent, files):
    for directory in {torrent['save_path'], torrent.get('download_path')} - {None, ''}:
        for file in files:
            path = Path(directory)/relative(file['name'])
            yield path
            yield path.with_name(path.name+'.!qB')


class HTTP:
    def __init__(self, base, headers=None):
        try:
            self.url = urlsplit(base)
            if (self.url.scheme not in {"http", "https"} or not self.url.hostname
                    or self.url.username is not None or self.url.password is not None
                    or self.url.query or self.url.fragment or any(ord(c) < 32 for c in base)):
                raise ValueError()
            self.url.port
        except (TypeError, ValueError):
            raise Failure("invalid_service_url") from None
        self.headers = headers or {}

    def request(self, method, path, body=None, headers=None, query=None):
        target = self.url.path.rstrip("/") + path
        if query:
            target += "?" + urlencode(query)
        cls = http.client.HTTPSConnection if self.url.scheme == "https" else http.client.HTTPConnection
        connection = cls(self.url.hostname, self.url.port, timeout=getattr(self, "timeout", 300))
        try:
            connection.request(method, target, body, {**self.headers, **(headers or {})})
            response = connection.getresponse()
            # http.client never follows redirects; credentials stay at the configured origin.
            if not 200 <= response.status < 300:
                raise Failure(f"http_{response.status}")
            data = response.read(RESPONSE_LIMIT + 1)
            if len(data) > RESPONSE_LIMIT:
                raise Failure("response_too_large")
            return data, response.getheaders()
        except TimeoutError:
            raise Failure('upload_timeout' if method == 'PUT' else 'service_timeout') from None
        except (OSError, http.client.HTTPException, ValueError, UnicodeError):
            raise Failure("network_or_protocol") from None
        finally:
            connection.close()

    def json(self, method, path, value=None, query=None):
        body = None if value is None else json.dumps(value).encode()
        raw, _ = self.request(method, path, body, {"Content-Type": "application/json"}, query)
        try:
            return json.loads(raw)
        except (ValueError, UnicodeError):
            raise Failure("invalid_json") from None


class OpenList:
    mounts = ('/aliyun',)

    def __init__(self, url, token, mounts=('/aliyun',)):
        self.mounts = mounts
        self.http = HTTP(url, {"Authorization": token})
        self.tick = lambda: None

    def call(self, action, value):
        return self.result(self.http.json("POST", "/api/fs/" + action, value))

    @staticmethod
    def result(value):
        if not isinstance(value, dict) or type(value.get("code")) is not int:
            raise Failure("invalid_openlist_response")
        if value["code"] != 200:
            raise Failure(f"openlist_code_{value['code']}")
        return value.get("data")

    def entries(self, directory):
        result = {}
        total = None
        for page in range(1, 501):
            data = self.call("list", {"path": directory, "password": "", "page": page,
                                      "per_page": 200, "refresh": page == 1})
            if not isinstance(data, dict) or type(data.get("total")) is not int or "content" not in data:
                raise Failure("invalid_listing")
            if total is not None and data["total"] != total:
                raise Failure("inconsistent_listing")
            total = data["total"]
            items = [] if data["content"] is None else data["content"]
            if not isinstance(items, list):
                raise Failure("invalid_listing")
            for item in items:
                name = item.get("name") if isinstance(item, dict) else None
                if not isinstance(name, str) or not name:
                    raise Failure("invalid_listing")
                if name in result:
                    raise Failure("duplicate_remote_names")
                result[name] = item
            if len(result) == total:
                return list(result.values())
            if not items or len(result) > total:
                raise Failure("inconsistent_listing")
        raise Failure("directory_too_large")

    def find(self, path):
        p = remote_path(path, self.mounts)
        return next((item for item in self.entries(str(p.parent)) if item['name'] == p.name), None)

    def lookup(self, path):
        """Prove absence from healthy ancestor listings, including a missing parent."""
        path = remote_path(path, self.mounts)
        current = PurePosixPath('/') / path.parts[1]
        parts = path.parts[2:]
        if not parts:
            raise Failure('unsafe_remote_path')
        for index, name in enumerate(parts):
            current /= name
            item = self.find(str(current))
            if item is None:
                return None
            if index < len(parts)-1 and item.get('is_dir') is not True:
                raise Failure('remote_directory_conflict')
        return item

    def mkdir(self, directory):
        path = remote_path(directory, self.mounts)
        if len(path.parts) == 2:
            return
        self.mkdir(str(path.parent))
        item = self.find(str(path))
        if item is None:
            self.call("mkdir", {"path": str(path)})
            item = self.find(str(path))
        if not item or item.get("is_dir") is not True:
            raise Failure("remote_directory_conflict")

    def put(self, path, handle, size, sha1):
        digest = hashlib.sha1()
        sent = 0

        def chunks():
            nonlocal sent
            while chunk := handle.read(CHUNK):
                sent += len(chunk)
                if sent > size:
                    raise Failure("source_changed")
                digest.update(chunk)
                self.tick()
                yield chunk

        raw, _ = self.http.request("PUT", "/api/fs/put", chunks(), {
            "File-Path": quote(path, safe="/"), "As-Task": "true", "Overwrite": "false",
            "Content-Length": str(size), "Content-Type": "application/octet-stream",
            "X-File-Sha1": sha1, "X-Generate-Seed": "off",
        })
        try:
            data = self.result(json.loads(raw))
        except (ValueError, UnicodeError):
            raise Failure("invalid_json") from None
        if sent != size or digest.hexdigest() != sha1:
            raise Failure("source_changed")
        task_id = (data or {}).get('task', {}).get('id')
        if not isinstance(task_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', task_id):
            raise Failure('missing_upload_task')
        return task_id

    def upload_task(self, task_id):
        return self.result(self.http.json('POST', '/api/admin/task/upload/info', {}, query={'tid': task_id}))

    def tasks(self, kind='upload'):
        values = []
        for status in ('undone', 'done'):
            result = self.result(self.http.json('GET', '/api/admin/task/' + kind + '/' + status))
            if not isinstance(result, list) or any(not isinstance(task, dict) for task in result):
                raise Failure('invalid_upload_task')
            values.extend(result)
        return values

    def task(self, task_id, kind='upload'):
        return self.result(self.http.json('POST', '/api/admin/task/' + kind + '/info', {}, query={'tid': task_id}))

    def move(self, stage, dest):
        # shortcut: upstream Aliyun Move is not atomic no-clobber; these folders need one writer.
        # A second writer requires an isolated publishing namespace or an OpenList driver patch.
        return self.call("move", {"src_dir": str(PurePosixPath(stage).parent),
                                 "dst_dir": str(PurePosixPath(dest).parent),
                                 "names": [PurePosixPath(stage).name], "overwrite": False,
                                 "skip_existing": False})


def job_id(identity, dest):
    return hashlib.sha256((identity + "\0" + dest).encode()).hexdigest()[:32]


def candidate(identity, source, dest, size, *, mount='/aliyun', **metadata):
    if type(size) is not int or size <= 0:
        raise Failure("invalid_source_size")
    remote_path(dest, (mount,), ('Movies', 'TV'))
    return {"id": job_id(identity, dest), "source": str(source), "dest": dest, "size": size, "identity": identity,
            'torrent_hash': identity.split(':')[1] if identity.startswith(('movie:', 'manual-tv:')) else None,
            'tvdb_id': None, 'episode_keys': '[]', 'parent_id': None, 'download_source':str(source), 'added_on':0, **metadata}


DOWNLOADS = {'cinema-movies': ('movies', 'Movies', 'movie'),
             'cinema-tv': ('tv-manual', 'TV', 'manual-tv'),
             'sonarr-tv': ('tv', 'TV', 'tv')}


class Sources:
    def __init__(self, config, media, db=None):
        self.config, self.media = config, Path(media)
        self.mount, self.mounts = configured_mounts(config)
        self.db = db
        self.qb = HTTP(config["qb_url"], {"Referer": config["qb_url"].rstrip("/") + "/"})
        self.sonarr = HTTP(config["sonarr_url"], {"X-Api-Key": config["sonarr_api_key"]})
        self.emby = HTTP(config['emby_url'], {'X-Emby-Token':config['emby_token']}) if config.get('emby_token') else None

    def destination_mount(self, identity, suffix, added_on):
        if self.db is None or len(self.mounts) == 1:
            return self.mount
        # A subtitle may already be recorded even if discovery stopped before inserting its parent.
        matches = []
        for mount in self.mounts:
            key = job_id(identity, mount + '/' + suffix)
            if self.db.execute("""SELECT 1 FROM jobs WHERE (id=? OR parent_id=?)
                AND (added_on=0 OR ?=0 OR added_on=?) LIMIT 1""", (key, key, added_on, added_on)).fetchone():
                matches.append(mount)
        if len(matches) > 1:
            raise Failure('destination_reserved')
        return matches[0] if matches else self.mount

    def login_qb(self):
        credentials = urlencode({"username": self.config["qb_username"],
                                 "password": self.config["qb_password"]}).encode()
        raw, headers = self.qb.request("POST", "/api/v2/auth/login", credentials,
                                      {"Content-Type": "application/x-www-form-urlencoded"})
        cookies = http.cookies.SimpleCookie()
        cookies.load(self.qb.headers.get("Cookie", ""))
        for name, value in headers:
            if name.lower() == "set-cookie":
                cookies.load(value)
        sessions = [c for name, c in cookies.items() if re.fullmatch(r"SID|QBT_SID_[0-9]+", name) and c.value]
        if raw.strip() not in (b"", b"Ok.") or len(sessions) != 1:
            raise Failure("qb_login_failed")
        self.qb.headers["Cookie"] = sessions[0].key + "=" + sessions[0].value

    def downloads(self):
        self.login_qb()
        result = []
        for category in ('cinema-movies', 'cinema-tv'):
            directory, remote, identity = DOWNLOADS[category]
            torrents = self.qb.json("GET", "/api/v2/torrents/info",
                                    query={"filter": "completed", "category": category})
            for torrent in torrents:
                if (torrent.get("category") != category or torrent.get("progress") != 1
                        or torrent.get("amount_left") != 0
                        or torrent.get("state", "").lower().startswith(("checking", "moving", "error", "missing"))):
                    continue
                try:
                    save = Path(torrent["save_path"])
                    root = self.media / "downloads" / directory
                    # The save directory may be the allowed root itself.
                    if save != root:
                        local_path(save, root)
                    files = self.qb.json("GET", "/api/v2/torrents/files", query={"hash": torrent["hash"]})
                    selected = [f for f in files if f.get("priority", 0) > 0
                                and video(f["name"])]
                    if not selected or any(f.get("progress") != 1 for f in selected):
                        continue
                    title = re.sub(r"[\\/:\x00-\x1f]", "_", torrent["name"]).strip(". ")[:180]
                    title = title or ("torrent-" + torrent["hash"][:12])
                except (Failure, KeyError, TypeError, ValueError, OSError):
                    log(category, "invalid_source")
                    continue
                for file in selected:
                    try:
                        rel = relative(file['name'])
                        source = local_path(save.joinpath(*rel.parts), root)
                        clean = PurePosixPath(*rel.parts[1:]) if len(rel.parts) > 1 and rel.parts[0] == torrent['name'] else rel
                        source_id = identity + ':' + torrent['hash'] + ':' + str(file['index'])
                        suffix = f'{remote}/{title}/{clean}'
                        mount = self.destination_mount(source_id, suffix, torrent.get('added_on', 0))
                        item = candidate(source_id, source, f'{mount}/{suffix}', file['size'], mount=mount,
                                         added_on=torrent.get('added_on',0))
                        sidecars = [f for f in files if f.get('priority', 0) > 0 and subtitle_for(f['name'], file['name'])]
                        if any(f.get('progress') != 1 for f in sidecars):
                            continue
                        group = []
                        for sub in sidecars:
                            subrel = relative(sub['name'])
                            subsource = local_path(save.joinpath(*subrel.parts), root)
                            subclean = PurePosixPath(*subrel.parts[1:]) if len(subrel.parts) > 1 and subrel.parts[0] == torrent['name'] else subrel
                            group.append(candidate(identity + ':' + torrent['hash'] + ':' + str(sub['index']), subsource,
                                                    f'{mount}/{remote}/{title}/{subclean}', sub['size'], mount=mount,
                                                    parent_id=item['id'],added_on=item['added_on']))
                        result.extend([*group, item])
                    except (Failure, KeyError, TypeError, ValueError, OSError):
                        log(category, "invalid_source")
        return result

    def tv(self):
        if not self.qb.headers.get('Cookie'):
            self.login_qb()
        result, originals = [], {}
        torrents = self.qb.json('GET', '/api/v2/torrents/info', query={'filter': 'completed', 'category': 'sonarr-tv'})
        for torrent in torrents:
            if torrent.get('category') != 'sonarr-tv' or torrent.get('progress') != 1:
                continue
            files = self.qb.json('GET', '/api/v2/torrents/files', query={'hash': torrent['hash']})
            for file in files:
                try:
                    if file.get('priority', 0) <= 0 or not video(file['name']):
                        continue
                    path = local_path(Path(torrent['save_path']).joinpath(*relative(file['name']).parts), self.media / 'downloads/tv')
                    st = path.stat()
                    originals[(st.st_dev, st.st_ino)] = (torrent['hash'], path, files, file['name'],torrent.get('added_on',0))
                except (Failure, OSError, ValueError, TypeError, KeyError):
                    log('tv', 'invalid_source')
        for series in self.sonarr.json('GET', '/api/v3/series'):
            try:
                root = self.media / 'library/tv'
                series_path = local_path(series['path'], root)
                files = self.sonarr.json('GET', '/api/v3/episodefile', query={'seriesId': series['id']})
                episodes = self.sonarr.json('GET', '/api/v3/episode', query={'seriesId': series['id']}) if series.get('tvdbId') else []
            except (Failure, KeyError, TypeError, ValueError, OSError):
                log('tv', 'invalid_source')
                continue
            for file in files:
                try:
                    if file.get('seriesId') != series['id']:
                        raise Failure('series_mismatch')
                    rel = relative(file['relativePath'])
                    source = local_path(series_path.joinpath(*rel.parts), root)
                    if file.get('path') and Path(file['path']) != source:
                        raise Failure('episode_path_mismatch')
                    if not video(rel):
                        continue
                    st = source.stat()
                    original = originals.get((st.st_dev, st.st_ino))
                    keys = [(e['seasonNumber'], e['episodeNumber']) for e in episodes if e.get('episodeFileId') == file['id']]
                    source_id = 'tv:' + str(file['id'])
                    suffix = 'TV/' + source.relative_to(root).as_posix()
                    mount = self.destination_mount(source_id, suffix, original[4] if original else 0)
                    item = candidate(source_id, source, f'{mount}/{suffix}', file['size'], mount=mount,
                                     tvdb_id=series.get('tvdbId'), episode_keys=json.dumps(keys), torrent_hash=original[0] if original else None,
                                     download_source=str(original[1]) if original else str(source),added_on=original[4] if original else 0)
                    subs = [p for p in source.parent.iterdir() if subtitle_for(p.as_posix(), source.as_posix())]
                    sub_sources = {}
                    if original:
                        _, raw, raw_files, raw_name, _ = original
                        expected = [f for f in raw_files if f.get('priority', 0) > 0 and subtitle_for(f['name'], raw_name)]
                        for entry in expected:
                            original_sub = local_path(raw.parent / PurePosixPath(entry['name']).name, self.media/'downloads/tv')
                            with open_source(original_sub, self.media/'downloads/tv') as handle:
                                digest = hashlib.file_digest(handle,'sha1').hexdigest()
                            for sub in subs:
                                if sub in sub_sources or sub.stat().st_size != entry['size']:
                                    continue
                                with open_source(sub, root) as handle:
                                    if hashlib.file_digest(handle,'sha1').hexdigest() == digest:
                                        sub_sources[sub] = original_sub
                                        break
                        if len(sub_sources) != len(expected):
                            log('tv', 'subtitle_import_pending')
                            continue
                    group = []
                    for sub in subs:
                        sub = local_path(sub, root)
                        group.append(candidate('tv-sub:' + str(file['id']) + ':' + sub.name, sub,
                                                mount + '/TV/' + sub.relative_to(root).as_posix(), sub.stat().st_size, mount=mount,
                                                torrent_hash=item['torrent_hash'], parent_id=item['id'],
                                                download_source=str(sub_sources.get(sub,sub)),added_on=item['added_on'],tvdb_id=item['tvdb_id']))
                    result.extend([*group, item])
                except (Failure, KeyError, TypeError, ValueError, OSError):
                    log('tv', 'invalid_source')
        return result


def verify(item, job, require_sha1=False):
    if (not item or item.get("is_dir") is not False or type(item.get("size")) is not int
            or item["size"] != job["size"]):
        raise Failure("remote_size_mismatch")
    hashes = item.get("hash_info") or {}
    if not isinstance(hashes, dict):
        raise Failure("invalid_remote_hash")
    found_sha1 = False
    for kind, value in hashes.items():
        if kind.lower().replace("-", "") == "sha1" and value:
            found_sha1 = True
            if not isinstance(value, str) or value.lower() != job["sha1"]:
                raise Failure("remote_hash_mismatch")
    if require_sha1 and not found_sha1:
        raise Failure('remote_hash_unavailable')


class Uploader:
    def __init__(self, remote, state, media, mounts=('/aliyun',)):
        self.remote, self.media, self.state = remote, Path(media), Path(state)
        self.mounts = mounts
        self.library = Path('/library')
        self.activity = {'phase': 'starting', 'job_id': None, 'at': time.time()}
        self.discovery_issue = None
        # In-memory backoff for pending cancel/delete actions: (id, at) -> (same-code streak, next try).
        # Each attempt can stop torrents, re-hash every local file and page Emby, so a long wait
        # (e.g. for an Emby scan) must not repeat that work every 5 seconds.
        self.action_backoff, self.clock = {}, time.monotonic
        self.db = sqlite3.connect(Path(state) / "uploads.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, source TEXT NOT NULL, dest TEXT UNIQUE NOT NULL, size INTEGER NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending', attempt INTEGER NOT NULL DEFAULT 0,
            stage TEXT, sha1 TEXT, receipt TEXT, retry_at REAL NOT NULL DEFAULT 0,
            failures INTEGER NOT NULL DEFAULT 0, error TEXT)""")
        if "identity" not in {r[1] for r in self.db.execute("PRAGMA table_info(jobs)")}:
            self.db.execute("ALTER TABLE jobs ADD COLUMN identity TEXT")
        columns = {r[1] for r in self.db.execute('PRAGMA table_info(jobs)')}
        for name, definition in [('task_id', 'TEXT'), ('progress', 'REAL NOT NULL DEFAULT 0'),
                                 ('task_id_from_put', 'INTEGER NOT NULL DEFAULT 0')]:
            if name not in columns:
                self.db.execute('ALTER TABLE jobs ADD COLUMN ' + name + ' ' + definition)
        for name, definition in [('instance_id', 'TEXT'), ('restart_count', 'INTEGER NOT NULL DEFAULT 0'),
                                 ('request_at', 'REAL NOT NULL DEFAULT 0'), ('native_state', 'INTEGER'),
                                 ('retry_requested', 'INTEGER NOT NULL DEFAULT 0'), ('terminal_proven', 'INTEGER NOT NULL DEFAULT 0'),
                                 ('move_task_id', 'TEXT'), ('torrent_hash', 'TEXT'), ('tvdb_id', 'INTEGER'),
                                 ('episode_keys', "TEXT NOT NULL DEFAULT '[]'"), ('parent_id', 'TEXT'), ('download_source','TEXT'),
                                 ('hidden','INTEGER NOT NULL DEFAULT 0'),('added_on','INTEGER NOT NULL DEFAULT 0')]:
            if name not in columns:
                self.db.execute('ALTER TABLE jobs ADD COLUMN ' + name + ' ' + definition)
        create_archive(self.db)
        self.db.execute('CREATE INDEX IF NOT EXISTS jobs_parent ON jobs(parent_id)')
        self.db.commit()

    def job_path(self, job):
        dest = remote_path(job['dest'], self.mounts, ('Movies', 'TV'))
        if job.get('stage'):
            remote_path(job['stage'], ('/' + dest.parts[1],), ('.cinema-upload',))
        return dest

    def update(self, job, **values):
        with self.db:
            self.db.execute("UPDATE jobs SET " + ",".join(key + "=?" for key in values) + " WHERE id=?",
                            [*values.values(), job["id"]])
            if values.get('state', job.get('state')) == 'published':
                record_archive(self.db, {**job, **values})
                if job.get('parent_id'):
                    self.db.execute('UPDATE jobs SET retry_at=0 WHERE id=?', (job['parent_id'],))
        job.update(values)

    def touch(self, phase=None, job_id=None):
        self.activity = {'phase': phase or self.activity['phase'], 'job_id': job_id or self.activity['job_id'], 'at': time.time()}

    @staticmethod
    def instance():
        path = Path('/runtime/instance-id')
        if not path.exists():
            return None
        value = path.read_text(encoding='utf-8').strip()
        if not re.fullmatch(r'[a-fA-F0-9-]{36}', value):
            raise Failure('invalid_instance_marker')
        return value

    def add(self, item):
        dest = self.job_path(item)
        episode_keys(item.get('episode_keys'))
        row = self.db.execute("SELECT * FROM jobs WHERE dest=?", (item["dest"],)).fetchone()
        actions = task_actions(self.state/'uploads.sqlite3')
        if not row or row['id'] != item['id']:
            deleted = [a for a in actions if a['action'] == 'delete' and a['state'] == 'done']
            if deleted:
                suffix = '/' + '/'.join(dest.parts[2:])
                for mount in self.mounts:
                    previous = {**item, 'id': job_id(item['identity'], mount + suffix)}
                    if any(action_matches(previous, a) for a in deleted):
                        return
        with self.db:
            if row and any(row[k] != item[k] for k in ('id', 'source', 'size')):
                if (row['id'] == item['id'] or row['state'] != 'cancelled' or not row['hidden']
                        or (row['stage'] and not row['terminal_proven'])
                        or self.db.execute('SELECT 1 FROM archived WHERE job_id=?', (row['id'],)).fetchone()):
                    raise Failure('destination_reserved')
                self.db.execute('DELETE FROM jobs WHERE id=?', (row['id'],))
                row = None
            if row and item.get('added_on') and row['added_on'] and item['added_on']!=row['added_on']:
                self.db.execute("UPDATE jobs SET hidden=0,state=CASE WHEN state='cancelled' THEN 'pending' ELSE state END WHERE id=?",(item['id'],))
            self.db.execute('''INSERT OR IGNORE INTO jobs (id,source,dest,size,identity,torrent_hash,tvdb_id,episode_keys,parent_id,download_source)
                VALUES (:id,:source,:dest,:size,:identity,:torrent_hash,:tvdb_id,:episode_keys,:parent_id,:download_source)''', item)
            self.db.execute('''UPDATE jobs SET identity=COALESCE(identity,?),torrent_hash=COALESCE(?,torrent_hash),
                tvdb_id=COALESCE(?,tvdb_id),episode_keys=CASE WHEN ?!='[]' THEN ? ELSE episode_keys END,
                parent_id=COALESCE(?,parent_id),download_source=COALESCE(?,download_source),added_on=CASE WHEN ?>0 THEN ? ELSE added_on END WHERE id=?''',
                (item['identity'], item.get('torrent_hash'), item.get('tvdb_id'), item.get('episode_keys','[]'),
                 item.get('episode_keys','[]'), item.get('parent_id'), item.get('download_source'),item.get('added_on',0),item.get('added_on',0), item['id']))
            record_archive(self.db, dict(self.db.execute('SELECT * FROM jobs WHERE id=?', (item['id'],)).fetchone()))
            current=dict(self.db.execute('SELECT * FROM jobs WHERE id=?',(item['id'],)).fetchone())
            for action in actions:
                if action['state']=='done' and action['action'] in {'cancel','delete'} and action_matches(current,action):
                    self.db.execute("UPDATE jobs SET hidden=?,state=CASE WHEN state='pending' THEN 'cancelled' ELSE state END WHERE id=?",(int(action['action']=='delete'),item['id']))

    def restart_upload(self, job, manual=False):
        if not manual and job['restart_count'] >= 4:
            self.update(job, state='failed', error='upload_retry_exhausted', terminal_proven=1, retry_at=0)
            return
        self.update(job, state='pending', restart_count=0 if manual else job['restart_count'] + 1,
                    retry_requested=0, task_id=None, task_id_from_put=0, native_state=None, terminal_proven=0,
                    error=None, failures=0, retry_at=0 if manual else time.time() + min(1800, 60 * 2**job['restart_count']))

    def recover_upload(self, job, manual=False):
        self.touch('reconciling', job['id'])
        if not job.get('stage') or not job.get('sha1'):
            raise Failure('upload_unconfirmed')
        mount = '/' + self.job_path(job).parts[1]
        current = self.instance()
        changed = bool(current and job.get('instance_id') and current != job['instance_id'])
        if job.get('task_id'):
            try:
                self.poll_upload(job, manual=manual)
                return
            except Failure as error:
                if str(error) not in {'http_404', 'openlist_code_404'}:
                    raise
                if job.get('native_state') is not None or job.get('terminal_proven'):
                    self.update(job, native_state=None, terminal_proven=0)
        if not changed:
            path = str(PurePosixPath(job['stage']).parent).removeprefix(mount) or '/'
            expected = f"upload {PurePosixPath(job['stage']).name} to [{mount}]({path})"
            tasks = [t for t in self.remote.tasks() if t.get('name') == expected]
            if len(tasks) > 1:
                raise Failure('ambiguous_upload_task')
            if tasks:
                self.update(job, state='queued', task_id=tasks[0]['id'], task_id_from_put=0,
                            native_state=None, terminal_proven=0)
                self.poll_upload(job, manual=manual)
                return
        stage = self.remote.find(job['stage'])
        if stage:
            try:
                verify(stage, job, require_sha1=True)
            except Failure:
                if not changed:
                    raise Failure('upload_unconfirmed') from None
            else:
                receipt = json.dumps({'stage':job['stage'], 'size':job['size'], 'sha1':job['sha1'],
                                      'put_code':200, 'evidence':'remote_sha1', 'task_id':job.get('task_id'), 'verified_at':time.time()})
                self.update(job, state='uploaded', receipt=receipt, error=None, failures=0, retry_at=0, progress=100)
                return
        if changed:
            self.restart_upload(job, manual=manual)
            return
        raise Failure('upload_unconfirmed')

    def upload(self, job):
        self.touch('hashing', job['id'])
        dest = self.job_path(job)
        if self.remote.lookup(job["dest"]) is not None:
            raise Failure("destination_conflict")
        reserved = self.db.execute("""SELECT COALESCE(sum(size),0) FROM jobs
            WHERE state IN ('uploading','queued') AND error='upload_unconfirmed'""").fetchone()[0]
        if shutil.disk_usage(self.state).free < reserved + job['size'] + 2 * 1024 ** 3:
            raise Failure('upload_cache_space_low')
        if job['identity'] and job['identity'].startswith('manual-tv:'):
            if dest.parts[2] != 'TV':
                raise Failure('unsafe_remote_path')
            root = self.media / 'downloads/tv-manual'
        else:
            root = self.media / ("downloads/movies" if dest.parts[2] == 'Movies' else "library/tv")
        with open_source(job["source"], root) as handle:
            before = fingerprint(handle)
            if before[2] != job["size"]:
                raise Failure("source_size_mismatch")
            digest = hashlib.sha1()
            while chunk := handle.read(CHUNK):
                digest.update(chunk)
                self.touch()
            if fingerprint(handle) != before:
                raise Failure("source_changed")
            if job.get('sha1') and job['sha1'] != digest.hexdigest():
                raise Failure('source_changed')
            attempt = job["attempt"] + 1
            stage = f"/{dest.parts[1]}/.cinema-upload/{job['id']}-{attempt}/{dest.name}"
            self.remote.mkdir(str(PurePosixPath(stage).parent))
            if self.remote.find(stage) is not None:
                raise Failure("staging_conflict")
            self.update(job, state="uploading", attempt=attempt, stage=stage, sha1=digest.hexdigest(), receipt=None,
                        error=None, retry_at=0, progress=0, task_id=None, task_id_from_put=0, native_state=None,
                        instance_id=self.instance(), request_at=time.time(), terminal_proven=0)
            handle.seek(0)
            self.touch('caching', job['id'])
            self.remote.tick = self.touch
            task_id = self.remote.put(stage, handle, job["size"], job["sha1"])
            if fingerprint(handle) != before:
                raise Failure("source_changed")
            self.update(job, state='queued', task_id=task_id, task_id_from_put=1, failures=0, error=None, retry_at=0)
            self.poll_upload(job)

    def poll_upload(self, job, manual=False):
        self.job_path(job)
        self.touch('uploading', job['id'])
        if not job.get('task_id'):
            raise Failure('missing_upload_task')
        task = self.remote.upload_task(job['task_id'])
        if not isinstance(task, dict) or task.get('id') != job['task_id'] or type(task.get('state')) is not int:
            raise Failure('invalid_upload_task')
        state, progress = task['state'], task.get('progress', 0)
        if type(progress) not in (int,float) or not 0 <= progress <= 100:
            raise Failure('invalid_upload_task')
        self.update(job, progress=progress, native_state=state, retry_requested=0, terminal_proven=int(state == 2))
        if state == 4 and not manual:
            self.update(job, state='failed', error='upload_cancelled', terminal_proven=1, retry_at=0)
            return
        if state in {4,7}:
            self.restart_upload(job, manual=manual)
            return
        if state in {0,1,3,5,6,8,9}:
            self.update(job, state='queued', error='native_upload_retrying' if state in {5,8,9} else ('native_upload_stopping' if state in {3,6} else None), retry_at=0)
            return
        if state != 2:
            raise Failure('invalid_upload_task')
        # A recovered ID or an ID from an older database doesn't prove which PUT wrote the bytes.
        recovered = not job.get('task_id_from_put')
        try:
            verify(self.remote.find(job['stage']), job, require_sha1=recovered)
        except Failure as error:
            if recovered and str(error) == 'remote_hash_unavailable':
                raise Failure('upload_unconfirmed') from None
            raise
        receipt = {'stage':job['stage'], 'size':job['size'], 'sha1':job['sha1'],
                   'put_code':200, 'task_id':job['task_id'], 'native_task_state':2, 'verified_at':time.time()}
        if recovered:
            receipt['evidence'] = 'remote_sha1'
        self.update(job, state='uploaded', receipt=json.dumps(receipt), failures=0, error=None, retry_at=0, progress=100)

    def publish(self, job, finish_only=False):
        self.job_path(job)
        self.touch('publishing', job['id'])
        episode_keys(job.get('episode_keys'))
        if self.blocked(job) and not finish_only:
            raise Failure('task_cancelling')
        if self.db.execute("SELECT 1 FROM jobs WHERE parent_id=? AND state!='published'", (job['id'],)).fetchone():
            raise Failure('subtitle_pending')
        receipt = json.loads(job["receipt"] or "{}")
        if any(receipt.get(k) != job[k] for k in ("stage", "size", "sha1")) or receipt.get("put_code") != 200:
            raise Failure("missing_upload_receipt")
        if job.get('task_id') and (receipt.get('task_id') != job['task_id'] or (receipt.get('native_task_state') != 2 and receipt.get('evidence') != 'remote_sha1')):
            raise Failure('missing_upload_receipt')
        sha1_receipt = receipt.get('evidence') == 'remote_sha1'
        legacy_receipt = not job.get('task_id_from_put') and not sha1_receipt
        dest, stage = self.remote.find(job["dest"]), self.remote.find(job["stage"])
        if dest is not None:
            if job["state"] != "moving" or stage is not None:
                raise Failure("destination_conflict")
            verify(dest, job, require_sha1=sha1_receipt or legacy_receipt)
            self.update(job, state="published", error=None, failures=0, retry_at=0)
            log(job["id"], "published")
            return
        if stage is None:
            raise Failure("staging_missing")
        verify(stage, job, require_sha1=sha1_receipt or legacy_receipt)
        if job["state"] == "moving":
            if job.get('move_task_id'):
                task = self.remote.task(job['move_task_id'], 'move')
                if task.get('state') in {4,7}:
                    self.update(job, state='uploaded', move_task_id=None)
                    raise Failure('move_retry_ready')
            # An uncertain move is only observed; replay could clobber a provider-side move.
            raise Failure('move_pending')
        if finish_only:
            raise Failure('move_pending')
        with watch_lock(self.state/'task-controls'):
            if self.blocked(job):
                raise Failure('task_cancelling')
            if legacy_receipt:
                receipt['evidence'] = 'remote_sha1'
            self.update(job, state="moving", receipt=json.dumps(receipt) if legacy_receipt else job['receipt'])
        try:
            result = self.remote.move(job['stage'], job['dest'])
        except Failure as error:
            if str(error) in {'http_400','http_401','http_403','openlist_code_401','openlist_code_403'}:
                self.update(job, state='uploaded')
            raise
        tasks = (result or {}).get('tasks') or []
        if len(tasks) == 1 and isinstance(tasks[0].get('id'),str):
            self.update(job, move_task_id=tasks[0]['id'])
        # Move may return an asynchronous task; success is determined from refreshed remote state.
        self.publish(job)

    def blocked(self, job):
        return any(a['action'] in {'cancel','delete'} and action_matches(job,a)
                   for a in task_actions(self.state/'uploads.sqlite3'))

    def cancel_job(self, job):
        self.job_path(job)
        if job['state'] in {'published','cancelled'}:
            return True
        if job['state']=='moving':
            try:
                self.publish(job,finish_only=True)
            except Failure:
                if job['state']!='uploaded':
                    raise
            if job['state']=='published':
                return True
        if job['state'] in {'uploading','queued','failed'} and not job.get('terminal_proven'):
            if job.get('task_id'):
                try:
                    task=self.remote.upload_task(job['task_id'])
                except Failure as error:
                    if str(error) not in {'http_404','openlist_code_404'}:
                        raise
                    if not (self.instance() and job.get('instance_id') and self.instance()!=job['instance_id']):
                        raise Failure('upload_unconfirmed') from None
                else:
                    if not isinstance(task, dict):
                        raise Failure('invalid_upload_task')
                    if task.get('state') not in {2,4,7}:
                        if task.get('state') not in {3,6}:
                            self.remote.result(self.remote.http.json('POST','/api/admin/task/upload/cancel?'+urlencode({'tid':job['task_id']})))
                        return False
            elif job.get('stage'):
                self.recover_upload(job)
                if job['state'] not in {'pending','uploaded'}:
                    return False
        self.update(job,state='cancelled',error=None,retry_at=0,retry_requested=0,terminal_proven=1)
        return True

    def delete_cloud_and_index(self, jobs, payload, sources, action_id, action_at):
        if not sources.emby or not self.library.is_dir():
            raise Failure('delete_all_not_configured')
        libraries = sources.emby.json('GET', '/Library/VirtualFolders/Query')['Items']
        roots = set()
        for plan in payload['delete_plan']:
            dest = self.job_path(plan)
            roots.add('/library/' + '/'.join(dest.parts[1:3]))
            if not re.fullmatch(r'[a-fA-F0-9]{40}', plan.get('sha1') or ''):
                raise Failure('delete_all_unverified')
        selected = {}
        for root in roots:
            matches = [lib for lib in libraries if root in {p.rstrip('/') for p in lib['Locations']}]
            if len(matches) != 1:
                raise Failure('delete_all_not_configured')
            selected[matches[0]['ItemId']] = matches[0]
        # Validate the whole selection before deleting any cloud object.
        for plan in payload['delete_plan']:
            index = local_path(self.library / plan['dest'].lstrip('/'), self.library, missing_ok=True)
            if index.exists() and (not index.is_file() or index.stat().st_size > 1024*1024):
                raise Failure('delete_all_unsafe_index')
            if self.remote.lookup(plan['dest']) is not None:
                verify(self.remote.call('get', {'path':plan['dest'],'password':''}), plan, require_sha1=True)
        for plan in payload['delete_plan']:
            target = PurePosixPath(plan['dest'])
            if self.remote.lookup(plan['dest']) is not None:
                verify(self.remote.call('get', {'path':plan['dest'],'password':''}), plan, require_sha1=True)
                # shortcut: OpenList has no conditional remove; Movies/TV must keep one exclusive writer.
                self.remote.call('remove', {'dir':str(target.parent),'names':[target.name]})
                if self.remote.lookup(plan['dest']) is not None:
                    raise Failure('delete_all_waiting_cloud')
        selected_ids = {j['id'] for j in jobs}
        other_destinations = [r['dest'] for r in self.db.execute('SELECT id,dest FROM jobs') if r['id'] not in selected_ids]
        for parent in sorted({str(PurePosixPath(v['dest']).parent) for v in payload['delete_plan']}, reverse=True):
            directory = PurePosixPath(parent)
            while len(directory.parts)>3:
                if any(p.startswith(str(directory)+'/') for p in other_destinations):
                    break
                if self.remote.lookup(str(directory)) is not None:
                    if self.remote.entries(str(directory)):
                        break
                    self.remote.call('remove', {'dir':str(directory.parent),'names':[directory.name]})
                    if self.remote.lookup(str(directory)) is not None:
                        raise Failure('delete_all_waiting_cloud')
                directory = directory.parent
        for plan in payload['delete_plan']:
            index = local_path(self.library / plan['dest'].lstrip('/'), self.library, missing_ok=True)
            if index.exists():
                backup = local_path(self.state/'deleted-indexes'/hashlib.sha256((action_id+'\0'+str(action_at)).encode()).hexdigest()/plan['id'], self.state, missing_ok=True)
                backup.mkdir(parents=True,exist_ok=True)
                destination = local_path(backup/index.name, self.state, missing_ok=True)
                if destination.exists():
                    if destination.read_bytes()!=index.read_bytes():
                        raise Failure('delete_all_unsafe_index')
                    index.unlink()
                else:
                    shutil.move(str(index), str(destination))
                parent = index.parent
                while parent != self.library.joinpath(*PurePosixPath(plan['dest']).parts[1:3]):
                    try: parent.rmdir()
                    except OSError: break
                    parent = parent.parent
        if roots:
            if time.time()-payload.get('scan_at',0)>60:
                for lib in selected.values():
                    sources.emby.request('POST', '/Items/'+lib['ItemId']+'/Refresh?Recursive=true&ReplaceAllMetadata=false&ReplaceAllImages=false')
                payload['scan_at'] = time.time()
                with self.db:
                    self.db.execute('UPDATE task_actions SET payload=? WHERE id=?',(json.dumps(payload),action_id))
            removed = {'/library'+p['dest'] for p in payload['delete_plan']}
            for lib in selected.values():
                start = 0
                while True:
                    result = sources.emby.json('GET', '/Items', query={'ParentId':lib['ItemId'],'Recursive':'true',
                        'IncludeItemTypes':'Movie,Episode','Fields':'Path,MediaSources','StartIndex':start,'Limit':1000})
                    for item in result['Items']:
                        if removed.intersection([item.get('Path'),*[s.get('Path') for s in item.get('MediaSources',[])]]):
                            raise Failure('delete_all_waiting_scan')
                    start += len(result['Items'])
                    if start >= result['TotalRecordCount']:
                        break
                    if not result['Items']:
                        raise Failure('delete_all_waiting_scan')
            if any((self.library / p['dest'].lstrip('/')).exists() for p in payload['delete_plan']):
                raise Failure('delete_all_waiting_scan')

    ACTION_FREE_RETRIES, ACTION_MAX_DELAY = 3, 300

    def process_actions(self, sources):
        actions = task_actions(self.state/'uploads.sqlite3')
        live = {(a['id'], a['at']) for a in actions if a['state'] == 'pending'}
        self.action_backoff = {k: v for k, v in self.action_backoff.items() if k in live}
        for action in actions:
            if action['state'] != 'pending' or action['action'] not in {'cancel', 'delete'}:
                continue
            key = (action['id'], action['at'])
            if self.action_backoff.get(key, (0, 0))[1] > self.clock():
                continue
            with watch_lock(self.state), watch_lock(self.state/'task-controls'):
                row = self.db.execute('SELECT state,at FROM task_actions WHERE id=?', (action['id'],)).fetchone()
                if not row or tuple(row) != ('pending', action['at']):
                    continue
                try:
                    self.process_action(sources, action)
                except (Failure, OSError, ValueError, KeyError, TypeError) as error:
                    code = str(error) if isinstance(error, Failure) else 'task_check_failed'
                    state = 'blocked' if code in {'delete_scope_required', 'task_changed', 'delete_all_local_changed',
                                                   'cleanup_local_unverified', 'cleanup_episode_changed'} else 'pending'
                    with self.db:
                        self.db.execute('UPDATE task_actions SET state=?,error=? WHERE id=?', (state, code, action['id']))
                    if code != action['error']:
                        log(action['id'], code)
                    # The first few repeats stay immediate; a persistent identical wait backs off up to 5 minutes.
                    streak = self.action_backoff.get(key, (0, 0))[0] + 1 if code == action['error'] else 1
                    delay = 0 if streak <= self.ACTION_FREE_RETRIES else min(self.ACTION_MAX_DELAY, 5 * 2 ** (streak - self.ACTION_FREE_RETRIES))
                    self.action_backoff[key] = (streak, self.clock() + delay)
                else:
                    self.action_backoff.pop(key, None)

    def process_action(self, sources, action):
        payload = action['payload']
        delete = action['action'] == 'delete'
        delete_files = delete and payload['delete_files']
        if delete_files and ('delete_targets' not in payload or 'delete_plan' not in payload):
            raise Failure('delete_scope_required')
        jobs = [dict(row) for row in self.db.execute('SELECT * FROM jobs WHERE hidden=0')
                if action_matches(dict(row), action)]
        if delete_files:
            approved = sorted((delete_target(job) for job in jobs), key=lambda job: job['id'])
            if approved != payload['delete_targets']:
                raise Failure('task_changed')
        if not all([self.cancel_job(job) for job in jobs]):
            raise Failure('task_waiting_for_upload')

        torrents = {t['hash']: t for t in sources.qb.json('GET', '/api/v2/torrents/info')}
        active = []
        for original in payload['torrents']:
            current = torrents.get(original['hash'])
            if current is None:
                continue
            files = self.check_torrent_files(sources, current, original)
            folder = self.media/'downloads'/DOWNLOADS[current['category']][0]
            if Path(current['save_path']) != folder:
                local_path(current['save_path'], folder, missing_ok=True)
            interim = current.get('download_path')
            if interim and Path(interim) != self.media/'incomplete':
                local_path(interim, self.media/'incomplete', missing_ok=True)
            for file in files:
                local_path(Path(current['save_path'])/relative(file['name']), folder, missing_ok=True)
            self.qb_form(sources, 'stop', {'hashes':current['hash']})
            stopped = sources.qb.json('GET', '/api/v2/torrents/info', query={'hashes':current['hash']})
            if not stopped or torrent_instance(stopped[0]) != torrent_instance(original):
                raise Failure('task_changed')
            if stopped[0].get('state') not in {'stoppedDL','pausedDL','stoppedUP','pausedUP','error','missingFiles'}:
                raise Failure('cleanup_waiting_for_stop')
            active.append(original)
        if payload.get('monitored_episode_ids') and not payload.get('episodes_unmonitored'):
            series = sources.sonarr.json('GET', '/api/v3/series/'+str(payload['series_id']))
            if payload['group'] != 'series:'+str(series['tvdbId']):
                raise Failure('task_changed')
            try:
                ids = episodes_with_monitoring(sources.sonarr, series['id'], payload['monitored_episode_ids'], True)
            except TaskScopeChanged:
                raise Failure('task_changed') from None
            if not payload.get('episode_monitoring_prepared'):
                # Save the actual changes before the PUT. A lost response must keep this restore list.
                payload.update(monitored_episode_ids=ids, episode_monitoring_prepared=True)
                with self.db:
                    self.db.execute('UPDATE task_actions SET payload=? WHERE id=?',(json.dumps(payload),action['id']))
            if ids:
                sources.sonarr.json('PUT','/api/v3/episode/monitor',{'episodeIds':ids,'monitored':False})
            payload['episodes_unmonitored'] = True
            with self.db:
                self.db.execute('UPDATE task_actions SET payload=? WHERE id=?',(json.dumps(payload),action['id']))
        if payload.get('series_id'):
            busy = sources.sonarr.json('GET', '/api/v3/command')
            if any(c.get('status') in {'queued','started'} and c.get('name') in {
                'RssSync','MissingEpisodeSearch','EpisodeSearch','SeasonSearch','SeriesSearch',
                'DownloadedEpisodesScan','ManualImport','RefreshMonitoredDownloads'} for c in busy):
                raise Failure('task_waiting_for_import')

        if delete_files:
            approved_cloud = payload['delete_plan']
            if payload.get('delete_all') and approved_cloud != sorted(
                    ({key:job[key] for key in ('id','dest','size','sha1')}
                     for job in jobs if job['state'] == 'published'), key=lambda job: job['id']):
                raise Failure('task_changed')
            episode_files = self.episode_deletions(sources, jobs, payload.get('series_id'))
            episodes = [{'series_id': f['seriesId'], 'file_id': f['id'], 'path': f['path']} for f, _ in episode_files]
            paths, verified = self.check_local_deletion(sources, payload, jobs, torrents, active, episodes)
            if payload.get('delete_all'):
                self.delete_cloud_and_index(jobs, payload, sources, action['id'], action['at'])
            recheck_series_sidecars(sources, episodes, verified)
            for file, ids in episode_files:
                if ids:
                    sources.sonarr.json('PUT', '/api/v3/episode/monitor', {'episodeIds':ids,'monitored':False})
                sources.sonarr.request('DELETE', '/api/v3/episodefile/'+str(file['id']))

        if delete:
            for original in active:
                latest = sources.qb.json('GET', '/api/v2/torrents/info', query={'hashes':original['hash']})
                if latest:
                    self.check_torrent_files(sources, latest[0], original)
                    self.qb_form(sources, 'delete', {'hashes':original['hash'],'deleteFiles':str(delete_files).lower()})
            remaining = sources.qb.json('GET', '/api/v2/torrents/info')
            if any(t['hash'] in {v['hash'] for v in payload['torrents']} for t in remaining):
                raise Failure('cleanup_waiting_for_removal')
        if delete_files:
            for path, (root, job) in verified.items():
                if path.exists() or path.is_symlink():
                    if not matches_payload(path, root, job):
                        raise Failure('delete_all_local_changed')
                    path.unlink()
            if any(path.exists() for path in paths):
                raise Failure('cleanup_waiting_for_files')
        with self.db:
            if delete:
                ids = [(job['id'],) for job in jobs]
                if payload.get('delete_all'):
                    self.db.executemany('DELETE FROM archived WHERE job_id=?', ids)
                    self.db.executemany('DELETE FROM jobs WHERE id=?', ids)
                else:
                    self.db.executemany('UPDATE jobs SET hidden=1 WHERE id=?', ids)
            self.db.execute("UPDATE task_actions SET state='done',error=NULL WHERE id=?", (action['id'],))

    def check_torrent_files(self, sources, current, original):
        if (torrent_instance(current) != torrent_instance(original) or 'download_path' in original
                and (current.get('download_path') or '') != (original.get('download_path') or '')):
            raise Failure('task_changed')
        files = sources.qb.json('GET', '/api/v2/torrents/files', query={'hash':current['hash']})
        if [(f['name'], f['size']) for f in files] != [(f['name'], f['size']) for f in original['files']]:
            raise Failure('task_changed')
        return files

    def episode_deletions(self, sources, jobs, series_id):
        file_jobs = {int(j['identity'][3:]): j for j in jobs if re.fullmatch(r'tv:[0-9]+', j.get('identity') or '')}
        if not file_jobs:
            return []
        if not series_id:
            raise Failure('cleanup_episode_mapping_missing')
        files = sources.sonarr.json('GET', '/api/v3/episodefile', query={'seriesId':series_id})
        episodes = sources.sonarr.json('GET', '/api/v3/episode', query={'seriesId':series_id})
        result = []
        for file in files:
            job = file_jobs.get(file['id'])
            if job is None:
                continue
            if any((file.get('path') != job['source'], file.get('seriesId') != series_id, file.get('size') != job['size'])):
                raise Failure('cleanup_episode_changed')
            local_path(file['path'], self.media/'library/tv', missing_ok=True)
            result.append((file, [e['id'] for e in episodes if e.get('episodeFileId') == file['id']]))
        return result

    def protected_paths(self, sources, torrents, selected_hashes, selected_jobs):
        claimed = set()
        for torrent in torrents:
            if torrent['hash'] not in selected_hashes:
                files = sources.qb.json('GET', '/api/v2/torrents/files', query={'hash':torrent['hash']})
                claimed.update(torrent_paths(torrent, files))
        for row in self.db.execute('SELECT id,source,download_source FROM jobs'):
            if row['id'] not in selected_jobs:
                claimed.update(Path(p) for p in (row['source'],row['download_source']) if p)
        try:
            claimed.update(path.resolve() for path in list(claimed))
        except RuntimeError:
            raise Failure('unsafe_or_missing_source') from None
        return claimed

    def check_local_deletion(self, sources, payload, jobs, torrents, active, episodes):
        claimed = self.protected_paths(sources, torrents.values(), {t['hash'] for t in payload['torrents']},
                                       {j['id'] for j in jobs})
        qb_paths = {p for t in active for p in torrent_paths(t, t['files'])}
        native_paths = qb_paths | {Path(e['path']) for e in episodes}
        paths = {p for t in payload['torrents'] for p in torrent_paths(t, t['files'])}
        paths.update(series_sidecars(sources, episodes))
        known = {}
        for job in jobs:
            for value in {job['source'], job.get('download_source') or job['source']}:
                path = Path(value)
                paths.add(path)
                if job.get('sha1') or path not in known:
                    known[path] = job

        verified = {}
        roots = [self.media/'downloads'/v[0] for v in DOWNLOADS.values()] + [self.media/'incomplete', self.media/'library/tv']
        for path in paths:
            if not path.exists() and not path.is_symlink():
                continue
            root = next((root for root in roots if path.is_relative_to(root)), None)
            if root is None:
                raise Failure('unsafe_or_missing_source')
            local_path(path, root)
            if path in claimed:
                raise Failure('cleanup_shared_file')
            if not path.is_file():
                raise Failure('unsafe_or_missing_source')
            job = known.get(path)
            if job and job.get('sha1'):
                if not matches_payload(path, root, job):
                    raise Failure('delete_all_local_changed')
                verified[path] = root, job
            elif path not in native_paths:
                raise Failure('cleanup_local_unverified')
        return paths, verified

    @staticmethod
    def qb_form(sources, action, values):
        return sources.qb.request('POST','/api/v2/torrents/'+action,urlencode(values).encode(),{'Content-Type':'application/x-www-form-urlencoded'})

    def run(self, candidates, sources=None):
        if candidates is not None:
            self.discovery_issue = None
        available = set()
        for item in candidates or ():
            try:
                self.add(item)
                available.add(item["id"])
            except (Failure, ValueError, TypeError) as error:
                code = str(error) if isinstance(error, Failure) else 'invalid_episode_mapping'
                if not self.discovery_issue:
                    # ponytail: surface the first discovery conflict here; paginate conflicts if one is insufficient.
                    self.discovery_issue = {'dest':item['dest'],'error':code}
                log(item['id'], code)
        if sources:
            self.process_actions(sources)
        for row in self.db.execute("SELECT * FROM jobs WHERE state NOT IN ('published','cancelled') AND (state!='failed' OR retry_requested=1) AND retry_at<=? ORDER BY rowid", (time.time(),)).fetchall():
            job = dict(row)
            if self.blocked(job):
                continue
            if job['state'] == 'pending' and job['id'] not in available:
                continue
            try:
                self.job_path(job)
                if job['state'] in {'uploading','queued','failed'}:
                    self.recover_upload(job, manual=bool(job.get('retry_requested')))
                elif job['state'] == 'pending':
                    if self.db.execute("""SELECT 1 FROM jobs WHERE state IN ('uploading','queued')
                                          AND terminal_proven=0 AND error IS NOT 'upload_unconfirmed'
                                          AND id!=?""", (job['id'],)).fetchone():
                        self.update(job, state='pending', error=None, retry_at=0)
                        continue
                    # An interrupted PUT has no receipt. Its old staging directory stays quarantined.
                    self.upload(job)
                if job['state'] in {'uploaded', 'moving'}:
                    if job['state'] == 'uploaded':
                        self.remote.mkdir(str(PurePosixPath(job['dest']).parent))
                    self.publish(job)
            except (Failure, OSError, ValueError, TypeError, KeyError, AttributeError) as error:
                code = str(error) if isinstance(error, Failure) else 'local_io_error' if isinstance(error, OSError) else 'archive_job_invalid'
                failures = job["failures"] + 1
                self.update(job, error=code, failures=failures,
                            retry_at=time.time() + min(3600, 60 * 2 ** min(failures - 1, 6)))
                log(job["id"], code)


@contextlib.contextmanager
def single_process(state):
    with (Path(state) / "uploader.lock").open("a+b") as handle:
        try:
            if os.name == "posix":
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise Failure("already_running") from None
        yield


def reconcile_watches(sources, state, config_dir):
    from portal.versions import validate_profile
    with watch_lock(state):
        requests = read_requests(state)
        if not requests:
            return
        records = json.loads((Path(config_dir) / 'versions.json').read_text(encoding='utf-8'))
        series = sources.sonarr.json('GET', '/api/v3/series')
        profiles = sources.sonarr.json('GET', '/api/v3/releaseprofile')
        for tvdb, request in list(requests.items()):
            value = next((s for s in series if str(s['tvdbId']) == tvdb), None)
            if not value:
                continue
            try:
                validate_profile(records[tvdb], value, profiles)
                done = apply_scope(sources.sonarr, value, request['mode'], request.get('season'),
                                   archived_keys(Path(state)/'uploads.sqlite3', value['tvdbId']), request['at'])
                if done:
                    requests.pop(tvdb)
            except (ValueError,KeyError,Failure):
                request['error'] = 'watch_rule_invalid'
        save_requests(state, requests)


def disk_guard(sources, state, now=None):
    with watch_lock(Path(state)/'task-controls'):
        return _disk_guard(sources,state,now)


def _disk_guard(sources, state, now=None):
    now = time.time() if now is None else now
    path = Path(state) / 'disk-paused.json'
    paused = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    torrents = sources.qb.json('GET', '/api/v2/torrents/info')
    managed = [t for t in torrents if t.get('category') in DOWNLOADS and t.get('progress',0)<1
               and not t.get('state','').lower().startswith(('checking','moving','error','missing'))]
    stopped = {'stoppedDL','pausedDL','stoppedUP','pausedUP'}
    active = [t for t in managed if t.get('state') not in stopped]
    backlog = 0
    dbpath = Path(state) / 'uploads.sqlite3'
    if dbpath.exists():
        with contextlib.closing(sqlite3.connect(dbpath.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='jobs'").fetchone():
                backlog = db.execute("SELECT COALESCE(max(size),0) FROM jobs WHERE state IN ('pending','uploading')").fetchone()[0]
    disk = shutil.disk_usage(sources.media)
    reserve = max(5*1024**3, int(disk.total*.08))
    def required(values):
        remaining = sum(max(0,t.get('amount_left',0)) for t in values)
        cache = max([backlog] + [t.get('size',0) for t in values])
        return remaining + cache + reserve
    # Stop newest obligations first; completed seeding tasks and pre-existing manual pauses are untouched.
    for torrent in sorted(active, key=lambda t:t.get('added_on',0), reverse=True):
        if required(active) <= disk.free:
            break
        data = {'hashes':torrent['hash']}
        paused[torrent['hash']] = {'category':torrent['category'],'save_path':torrent['save_path'],
                                   'added_on':torrent.get('added_on'), 'at':now}
        private_write(path,json.dumps(paused))
        sources.qb.request('POST','/api/v2/torrents/stop',urlencode(data).encode(),{'Content-Type':'application/x-www-form-urlencoded'})
        active.remove(torrent)
    for key,record in list(paused.items()):
        torrent = next((t for t in managed if t['hash']==key),None)
        if not torrent or any(record[k]!=torrent.get(k) for k in ('category','save_path','added_on')):
            paused.pop(key)
        elif torrent.get('state') in stopped and required(active+[torrent])+1024**3 <= disk.free:
            sources.qb.request('POST','/api/v2/torrents/start',urlencode({'hashes':key}).encode(),{'Content-Type':'application/x-www-form-urlencoded'})
            paused.pop(key)
            active.append(torrent)
        else:
            continue
        private_write(path,json.dumps(paused))
    return len(paused)


def monitor(uploader, config_dir, stop):
    config_path = Path(config_dir)/'config.json'
    active_config, sources = None, None
    while not stop.is_set():
        status = {'at':time.time(), 'activity':uploader.activity, 'discovery_issue':uploader.discovery_issue,
                  'disk_paused':0, 'error':None}
        try:
            config = load_config(config_path)
            if config is None:
                raise Failure('waiting_for_setup')
            if sources is None or config != active_config:
                sources = Sources(config, uploader.media)
                sources.qb.timeout = 15
                sources.login_qb()
                active_config = config
            status['disk_paused']=disk_guard(sources,uploader.state)
        except (Failure,OSError,ValueError,KeyError,TypeError,sqlite3.Error):
            sources = None
            status['error']='disk_check_unavailable'
        try:
            private_write(uploader.state/'heartbeat.json',json.dumps(status))
        except OSError:
            pass
        stop.wait(10)


def load_config(path):
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise Failure("invalid_configuration")
    keys = ("openlist_url", "openlist_token", "qb_url", "qb_username", "qb_password", "sonarr_url", "sonarr_api_key")
    for key in keys:
        if key in config and (not isinstance(config[key], str) or any(ord(c) < 32 for c in config[key])):
            raise Failure("invalid_configuration")
    config['cloud_mount'], config['cloud_mounts'] = configured_mounts(config)
    if not all(config.get(key) for key in keys):
        return None
    return config


def main():
    os.umask(0o077)
    state, media = Path("/state"), Path("/media")
    state.mkdir(exist_ok=True)
    with single_process(state):
        config_path = Path("/config/config.json")
        while True:
            config = load_config(config_path) if config_path.is_file() else None
            if config:
                break
            log("uploader", "waiting_for_setup")
            if "--once" in sys.argv:
                return
            time.sleep(30)
        mounts = config['cloud_mounts']
        uploader = Uploader(OpenList(config["openlist_url"], config["openlist_token"], mounts), state, media, mounts)
        sources = Sources(config, media, uploader.db)
        sys.modules.setdefault("uploader", sys.modules[__name__])
        from cleanup import Cleaner
        cleaner = Cleaner(uploader, state, Path('/config/settings.json'))
        stop = threading.Event()
        observer = threading.Thread(target=monitor,args=(uploader,Path('/config'),stop),daemon=True)
        observer.start()
        active_config, next_discovery = config, 0
        try:
            while True:
                try:
                    config = load_config(config_path)
                except (Failure, OSError, ValueError):
                    log("uploader", "config_reload_deferred")
                    config = active_config
                if config is None:
                    log("uploader", "waiting_for_setup")
                    if "--once" in sys.argv:
                        break
                    time.sleep(30)
                    continue
                if config != active_config:
                    uploader.mounts = config['cloud_mounts']
                    uploader.remote = OpenList(config["openlist_url"], config["openlist_token"], uploader.mounts)
                    sources = Sources(config, media, uploader.db)
                    active_config, next_discovery = config, 0
                discover = time.monotonic() >= next_discovery
                candidates = None
                if discover:
                    candidates = []
                    uploader.touch('discovering')
                    try:
                        reconcile_watches(sources,state,Path('/config'))
                    except (Failure,OSError,ValueError,KeyError,TypeError):
                        log('watch','reconcile_deferred')
                    for name, collect in (("downloads", sources.downloads), ("tv", sources.tv)):
                        try:
                            candidates.extend(collect())
                        except (Failure, OSError, KeyError, TypeError, ValueError):
                            log(name, "discovery_failed")
                uploader.run(candidates,sources)
                if discover:
                    try:
                        uploader.touch('cleaning')
                        cleaner.run(sources)
                    except (Failure, OSError, ValueError, KeyError, TypeError):
                        log("cleanup", "deferred_check_failed")
                    next_discovery = time.monotonic()+60
                uploader.touch('idle')
                if "--once" in sys.argv:
                    break
                busy = uploader.db.execute("SELECT 1 FROM jobs WHERE state='queued' UNION SELECT 1 FROM task_actions WHERE state='pending'").fetchone()
                time.sleep(min(5 if busy else 60, max(0, next_discovery-time.monotonic())))
        finally:
            stop.set()
            observer.join(timeout=1)
            uploader.db.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception:
        # Do not emit tracebacks: HTTP exceptions and configuration errors can contain secrets.
        log("uploader", "fatal_configuration_or_state_error")
        sys.exit(1)
